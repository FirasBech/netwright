"""Netwright project file (``.netwright``) — single UTF-8 JSON document.

A topology is high-value user data, so the loader is conservative: it parses
into a *fresh* object and never overwrites the original on a failed/partial
load, and it refuses to open a file written by a newer Netwright (unknown future
schema version) rather than silently dropping data. A ``migrate()`` chain
upgrades older files; the v1 -> v2 step converts ``vlans`` from a list to a
tag-keyed dict.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from .model import Topology

SCHEMA = "netwright.project"
SCHEMA_VERSION = 2


class ProjectError(Exception):
    """Raised when a project file cannot be opened safely."""


@dataclass
class NetwrightProject:
    name: str = "Untitled"
    topology: Topology = field(default_factory=Topology)
    view: dict = field(default_factory=lambda: {"zoom": 1.0, "center": [0, 0]})
    created: str = ""
    modified: str = ""

    def to_dict(self) -> dict:
        return {
            "schema": SCHEMA,
            "version": SCHEMA_VERSION,
            "name": self.name,
            "created": self.created,
            "modified": self.modified,
            "topology": self.topology.to_dict(),
            "view": dict(self.view),
        }

    @classmethod
    def from_dict(cls, doc: dict) -> "NetwrightProject":
        return cls(
            name=doc.get("name", "Untitled"),
            topology=Topology.from_dict(doc.get("topology", {})),
            view=dict(doc.get("view", {"zoom": 1.0, "center": [0, 0]})),
            created=doc.get("created", ""),
            modified=doc.get("modified", ""),
        )

    # ---- persistence -------------------------------------------------------
    @classmethod
    def load(cls, path: str | Path) -> "NetwrightProject":
        try:
            with open(path, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except OSError as exc:
            raise ProjectError(f"Cannot read {path}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ProjectError(f"{path} is not valid JSON: {exc}") from exc

        if not isinstance(doc, dict):
            raise ProjectError(f"{path} is not a Netwright project.")

        version = doc.get("version", 1)
        if not isinstance(version, int):
            raise ProjectError(f"{path} has an invalid version field.")
        if version > SCHEMA_VERSION:
            raise ProjectError(
                f"{path} was made by a newer Netwright (schema v{version}); "
                f"this build understands up to v{SCHEMA_VERSION}."
            )
        doc = migrate(doc)
        return cls.from_dict(doc)

    def save(self, path: str | Path) -> None:
        """Atomically write the project (temp file + os.replace)."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.to_dict(), fh, indent=2, sort_keys=True)
            os.replace(tmp, path)
        except Exception:
            # Don't leave a partial temp file behind on a failed serialize/replace.
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise


# --------------------------------------------------------------------------
# Migration chain. Each step upgrades a doc by exactly one version.
# --------------------------------------------------------------------------
def migrate(doc: dict) -> dict:
    version = doc.get("version", 1)
    if version < 2:
        doc = _v1_to_v2(doc)
    doc["version"] = SCHEMA_VERSION
    doc.setdefault("schema", SCHEMA)
    return doc


def _v1_to_v2(doc: dict) -> dict:
    """v1 stored ``topology.vlans`` as a list; v2 keys it by VLAN id (string)."""
    topo = doc.get("topology", {})
    vlans = topo.get("vlans")
    if isinstance(vlans, list):
        topo["vlans"] = {str(v["id"]): v for v in vlans if "id" in v}
        doc["topology"] = topo
    doc["version"] = 2
    return doc
