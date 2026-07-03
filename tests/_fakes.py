"""Hand-rolled Anthropic fakes so the suite runs offline with no SDK or key."""
from __future__ import annotations

from types import SimpleNamespace


def make_tool_use(name: str, tool_input: dict, block_id: str = "toolu_1"):
    return SimpleNamespace(type="tool_use", name=name, input=tool_input, id=block_id)


def make_text(text: str):
    return SimpleNamespace(type="text", text=text)


class _Messages:
    def __init__(self, response):
        self._response = response
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return self._response


class FakeClaudeClient:
    """Mimics ``anthropic.Anthropic``: ``.messages.create(**kw) -> Message``."""

    def __init__(self, content_blocks, stop_reason="tool_use"):
        response = SimpleNamespace(content=list(content_blocks), stop_reason=stop_reason)
        self.messages = _Messages(response)


def propose_response(summary: str, ops: list[dict], rationale: str = ""):
    """A canned propose_topology_changes tool_use response."""
    return FakeClaudeClient(
        [
            make_tool_use(
                "propose_topology_changes",
                {"summary": summary, "rationale": rationale, "ops": ops},
            )
        ]
    )
