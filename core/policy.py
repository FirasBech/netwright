"""Vendored copy of SecureLink's VLAN policy engine.

Netwright does not import from SecureLink; it vendors this engine so the two
projects stay decoupled while remaining file-compatible. ``config/vlan_policy.json``
is a deny-by-default source-VLAN -> [allowed destination VLANs] map with string
keys on disk, e.g. ``{"10": [10, 20], "20": [20], "30": [10, 30]}``. Netwright
*exports* a policy file in this exact shape so SecureLink's runtime guard can
consume it unchanged — that is the real interop story.
"""
from __future__ import annotations

import json
from pathlib import Path


class VlanPolicyEngine:
    """Deny-by-default inter-VLAN policy.

    A source VLAN absent from the map is denied to every destination. The loader
    int-coerces keys/values and silently skips malformed entries so a hand-edited
    file never crashes the engine.
    """

    def __init__(self, policy: dict[int, list[int]] | None = None) -> None:
        self._policy: dict[int, set[int]] = {}
        if policy:
            for src, dsts in policy.items():
                try:
                    self._policy[int(src)] = {int(d) for d in dsts}
                except (TypeError, ValueError):
                    continue

    @classmethod
    def from_file(cls, path: str | Path) -> "VlanPolicyEngine":
        raw = cls._load_policy(path)
        return cls(raw)

    @staticmethod
    def _load_policy(path: str | Path) -> dict[int, list[int]]:
        """Defensively load the policy file, skipping malformed entries."""
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        result: dict[int, list[int]] = {}
        for src, dsts in data.items():
            try:
                key = int(src)
            except (TypeError, ValueError):
                continue
            if not isinstance(dsts, (list, tuple)):
                continue
            allowed: list[int] = []
            for d in dsts:
                try:
                    allowed.append(int(d))
                except (TypeError, ValueError):
                    continue
            result[key] = allowed
        return result

    def is_allowed(self, src: int, dst: int) -> bool:
        """Return True iff traffic from ``src`` to ``dst`` is permitted."""
        return dst in self._policy.get(int(src), set())

    def as_dict(self) -> dict[str, list[int]]:
        """Serialize back to the on-disk shape (string keys, sorted)."""
        return {
            str(src): sorted(dsts)
            for src, dsts in sorted(self._policy.items())
        }

    def permit_pairs(self) -> list[tuple[int, int]]:
        """Every (src, dst) the policy permits, sorted."""
        return sorted(
            (src, dst) for src, dsts in self._policy.items() for dst in dsts
        )


def acls_from_policy(engine: "VlanPolicyEngine") -> list:
    """Translate a policy engine's permits into Netwright permit ACL rules."""
    from .model import AclRule

    return [AclRule(src, dst, "permit") for src, dst in engine.permit_pairs()]


def import_policy_file(path: str | Path) -> list:
    """Load a SecureLink-style ``vlan_policy.json`` as a list of permit ACLs."""
    return acls_from_policy(VlanPolicyEngine.from_file(path))
