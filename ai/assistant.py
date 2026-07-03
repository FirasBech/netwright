"""Assistant flow: propose (single-shot) and explain (templated fallback).

The model proposes ops; the app stages them on a *scratch copy* of the topology,
runs the deterministic ``core.validate`` on that copy, and returns a
:class:`ProposedChange`. Nothing touches the live canvas — the UI shows a diff
and the user approves before the batch is applied as one undo step.
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field

from core.commands import CommandStack
from core.model import Topology
from core.validate import Issue, validate

from .client import NetwrightAI
from .prompts import SYSTEM
from .tools import (
    PROPOSE_TOOL,
    TOOL_CHOICE,
    destructive_count,
    ops_to_commands,
    validate_ops,
)

# Reject a proposal that would delete more than this fraction of the document.
DESTRUCTIVE_FRACTION_LIMIT = 0.5


@dataclass
class ProposedChange:
    summary: str
    rationale: str
    ops: list[dict]
    op_issues: list[Issue] = field(default_factory=list)
    predicted_issues: list[Issue] = field(default_factory=list)
    destructive: int = 0

    @property
    def has_errors(self) -> bool:
        # The hard gate is op well-formedness. predicted_issues (full validation
        # of the resulting design) is advisory — apply-time checks whether the
        # change INTRODUCES new errors, so a design that already has issues can
        # still be improved incrementally.
        return any(i.severity == "error" for i in self.op_issues)

    @property
    def applicable(self) -> bool:
        return bool(self.ops) and not self.has_errors

    def is_too_destructive(self, topology: Topology) -> bool:
        total = max(1, len(topology.devices))
        return self.destructive / total > DESTRUCTIVE_FRACTION_LIMIT


def _extract_tool_input(message) -> dict:
    """Pull the propose_topology_changes input from a Message-shaped object."""
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    for block in content or []:
        btype = getattr(block, "type", None) or (
            block.get("type") if isinstance(block, dict) else None
        )
        if btype == "tool_use":
            data = getattr(block, "input", None)
            if data is None and isinstance(block, dict):
                data = block.get("input")
            return data or {}
    return {}


def _topology_json(topology: Topology) -> str:
    return json.dumps(topology.to_dict(), sort_keys=True)


def propose_change(
    topology: Topology, intent: str, ai: NetwrightAI
) -> ProposedChange:
    """Single-shot: one forced-tool call -> staged, validated ProposedChange."""
    message = ai.create_message(
        system=[
            {"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}
        ],
        messages=[
            {
                "role": "user",
                "content": _topology_json(topology) + "\n\nRequest: " + intent,
            }
        ],
        tools=[PROPOSE_TOOL],
        tool_choice=TOOL_CHOICE,
    )
    data = _extract_tool_input(message)
    raw_ops = data.get("ops", [])
    # Guard: a non-list would be char-iterated by list(); keep only dict ops.
    ops = [o for o in raw_ops if isinstance(o, dict)] if isinstance(raw_ops, list) else []

    op_issues = validate_ops(topology, ops)

    # Stage on a scratch copy and run the deterministic validator.
    scratch = Topology.from_dict(copy.deepcopy(topology.to_dict()))
    stack = CommandStack(scratch)
    try:
        for cmd in ops_to_commands(scratch, ops):
            stack.execute(cmd)
        predicted = validate(scratch)
    except Exception as exc:  # a malformed op should not crash the app
        op_issues.append(Issue("error", "OP_APPLY_FAILED", str(exc)))
        predicted = []

    return ProposedChange(
        summary=data.get("summary", ""),
        rationale=data.get("rationale", ""),
        ops=ops,
        op_issues=op_issues,
        predicted_issues=predicted,
        destructive=destructive_count(ops),
    )


def explain(topology: Topology, ai: NetwrightAI | None = None) -> str:
    """Explain the current design. Uses Claude if available, else a template."""
    if ai is not None and ai.available():
        try:
            message = ai.create_message(
                system=[{"type": "text", "text": SYSTEM}],
                messages=[
                    {
                        "role": "user",
                        "content": _topology_json(topology)
                        + "\n\nExplain this design in plain language.",
                    }
                ],
                tools=[PROPOSE_TOOL],
                tool_choice={"type": "auto"},
            )
            text = _first_text(message)
            if text:
                return text
        except Exception:
            pass  # fall through to the deterministic template
    return _templated_explain(topology)


def _first_text(message) -> str:
    content = getattr(message, "content", None) or (
        message.get("content") if isinstance(message, dict) else []
    )
    for block in content or []:
        btype = getattr(block, "type", None) or (
            block.get("type") if isinstance(block, dict) else None
        )
        if btype == "text":
            return getattr(block, "text", "") or (
                block.get("text", "") if isinstance(block, dict) else ""
            )
    return ""


def _templated_explain(topology: Topology) -> str:
    issues = validate(topology)
    errors = sum(1 for i in issues if i.severity == "error")
    warnings = sum(1 for i in issues if i.severity == "warning")
    lines = [
        f"Design '{topology.name}' has {len(topology.devices)} device(s), "
        f"{len(topology.links)} link(s), and {len(topology.vlans)} VLAN(s).",
    ]
    if topology.vlans:
        names = ", ".join(
            f"VLAN {v.id} ({v.name})" for v in sorted(
                topology.vlans.values(), key=lambda v: v.id
            )
        )
        lines.append("VLANs: " + names + ".")
    lines.append(
        f"Validation: {errors} error(s), {warnings} warning(s)."
        if issues
        else "Validation: no issues found."
    )
    return " ".join(lines)
