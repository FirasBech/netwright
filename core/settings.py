"""Per-user settings, vendored from SecureLink's plain-dataclass pattern.

Stored at ``~/.netwright/settings.json``. The loader is defensive: it returns a
default ``Settings()`` on any read/parse error rather than raising, because
settings are low-value and recoverable (unlike a project file). The API key is
read from the environment first and is *never* written into a project file,
autosave, undo history, or log.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


def _default_base_dir() -> Path:
    return Path.home() / ".netwright"


@dataclass
class Settings:
    anthropic_api_key: str | None = None  # env ANTHROPIC_API_KEY overrides this
    model: str = "claude-opus-4-8"
    theme: str = "dark"
    recent_projects: list[str] = field(default_factory=list)

    # ---- persistence -------------------------------------------------------
    @staticmethod
    def path(base_dir: str | Path | None = None) -> Path:
        base = Path(base_dir) if base_dir else _default_base_dir()
        return base / "settings.json"

    @classmethod
    def load(cls, base_dir: str | Path | None = None) -> "Settings":
        try:
            with open(cls.path(base_dir), "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                return cls()
        except (OSError, json.JSONDecodeError):
            return cls()
        return cls(
            anthropic_api_key=data.get("anthropic_api_key"),
            model=data.get("model", "claude-opus-4-8"),
            theme=data.get("theme", "dark"),
            recent_projects=list(data.get("recent_projects", [])),
        )

    def save(self, base_dir: str | Path | None = None) -> None:
        p = self.path(base_dir)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(self.as_dict(), fh, indent=2, sort_keys=True)

    def as_dict(self) -> dict:
        return {
            "anthropic_api_key": self.anthropic_api_key,
            "model": self.model,
            "theme": self.theme,
            "recent_projects": list(self.recent_projects),
        }

    # ---- key resolution ----------------------------------------------------
    def resolve_api_key(self) -> str | None:
        """Env var wins over the stored value; returns None if neither is set."""
        return os.environ.get("ANTHROPIC_API_KEY") or self.anthropic_api_key
