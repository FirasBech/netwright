"""Thin, offline-safe wrapper around the Anthropic SDK.

The ``import anthropic`` is guarded exactly like SecureLink guards optional
deps, so the whole app imports and runs with the SDK absent. ``available()``
returns False when either the SDK or an API key is missing — the UI uses that to
disable Propose while keeping design / validate / export / templated-explain
fully working.

Model facts pinned for ``claude-opus-4-8`` (verify against the claude-api
reference before changing): use ``thinking={"type": "adaptive"}``; do NOT send
``temperature``, ``top_p``, ``top_k``, ``budget_tokens``, or an assistant
prefill message — each returns HTTP 400 on this model.
"""
from __future__ import annotations

import os

try:  # offline-safe optional dependency
    import anthropic
except ImportError:  # pragma: no cover - exercised by test_ai_offline
    anthropic = None  # type: ignore[assignment]

MODEL = "claude-opus-4-8"
MAX_TOKENS = 8000

# Per-1M-token pricing for the cost panel (USD).
PRICE_INPUT_PER_MTOK = 5.00
PRICE_OUTPUT_PER_MTOK = 25.00


class AIError(Exception):
    """Raised for a surfaced, user-friendly AI failure."""


class NetwrightAI:
    """Wraps the Anthropic client; the client is injectable for tests."""

    def __init__(self, api_key: str | None = None, client=None, model: str = MODEL):
        self.model = model
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self._client = client  # tests inject a FakeClaudeClient here

    def available(self) -> bool:
        if self._client is not None:
            return True
        return anthropic is not None and bool(self._api_key)

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        if anthropic is None:
            raise AIError(
                "The 'anthropic' package is not installed. Run: pip install anthropic"
            )
        if not self._api_key:
            raise AIError("Set ANTHROPIC_API_KEY to enable the assistant.")
        self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    def create_message(self, *, system, messages, tools, tool_choice):
        """Call messages.create with the pinned, 4.8-safe parameter set."""
        client = self._ensure_client()
        try:
            return client.messages.create(
                model=self.model,
                max_tokens=MAX_TOKENS,
                system=system,
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
                thinking={"type": "adaptive"},
            )
        except Exception as exc:  # surface SDK errors as friendly AIError
            if anthropic is not None and isinstance(
                exc, getattr(anthropic, "AuthenticationError", ())
            ):
                raise AIError("Authentication failed — check ANTHROPIC_API_KEY.") from exc
            if anthropic is not None and isinstance(
                exc, getattr(anthropic, "APIConnectionError", ())
            ):
                raise AIError("Could not reach the Anthropic API.") from exc
            if anthropic is not None and isinstance(
                exc, getattr(anthropic, "RateLimitError", ())
            ):
                raise AIError("Rate limited — try again shortly.") from exc
            raise AIError(str(exc)) from exc
