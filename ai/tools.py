"""The single AI tool: ``propose_topology_changes``.

The model edits the topology only by emitting a list of typed ``ops`` through
this one strict tool (forced via ``tool_choice``). ``strict: true`` guarantees
argument *shape*; this module's :func:`validate_ops` enforces *semantics*
(referenced ids exist, VLAN 1..4094, CIDR parses, ACL action valid), and
:func:`ops_to_commands` turns each op into a ``core.commands`` Command so the AI
mutates state only through the same funnel as the UI.
"""
from __future__ import annotations

import ipaddress

from core import commands as C
from core.ids import new_id
from core.model import VLAN_MAX, VLAN_MIN, AclRule, Device, Link, Port, Topology
from core.validate import Issue

# Ops that remove or destructively re-address existing state.
DESTRUCTIVE_OPS = {"remove_device", "remove_link", "delete_vlan", "remove_acl"}

KNOWN_OPS = DESTRUCTIVE_OPS | {
    "add_device",
    "set_device",
    "add_link",
    "create_vlan",
    "edit_vlan",
    "set_port_vlan",
    "set_trunk",
    "add_acl",
    "set_subnet",
    "rename",
}

PROPOSE_TOOL = {
    "name": "propose_topology_changes",
    "description": (
        "Propose a batch of edits to the network topology. Emit a list of ops; "
        "the application validates and shows the user a diff before applying."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["summary", "ops"],
        "properties": {
            "summary": {"type": "string", "description": "One-line summary."},
            "rationale": {"type": "string", "description": "Why these changes."},
            "ops": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": True,
                    "required": ["op"],
                    "properties": {
                        "op": {"type": "string", "enum": sorted(KNOWN_OPS)},
                    },
                },
            },
        },
    },
}

TOOL_CHOICE = {"type": "tool", "name": "propose_topology_changes"}


def _err(code: str, msg: str) -> Issue:
    return Issue("error", code, msg)


def _vlan_ok(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and VLAN_MIN <= v <= VLAN_MAX


def validate_ops(topology: Topology, ops: list[dict]) -> list[Issue]:
    """Semantic validation of a proposed op list (untrusted AI output)."""
    issues: list[Issue] = []
    # Track ids that will exist as ops are applied in order.
    known_devices = set(topology.devices.keys())
    known_vlans = set(topology.vlans.keys())

    for i, op in enumerate(ops):
        if not isinstance(op, dict):
            issues.append(_err("OP_MALFORMED", f"op #{i}: expected an object."))
            continue
        kind = op.get("op")
        if kind not in KNOWN_OPS:
            issues.append(_err("OP_UNKNOWN", f"op #{i}: unknown op '{kind}'."))
            continue

        if kind in ("create_vlan", "edit_vlan", "delete_vlan"):
            vid = op.get("id")
            if not _vlan_ok(vid):
                issues.append(
                    _err("OP_BAD_VLAN", f"op #{i}: VLAN id {vid!r} out of range.")
                )
            elif kind == "create_vlan":
                known_vlans.add(vid)

        if kind == "add_device":
            dev_id = op.get("id") or f"pending-{i}"
            known_devices.add(dev_id)

        if kind in ("set_port_vlan", "set_trunk", "set_device", "rename"):
            dev = op.get("device")
            if dev not in known_devices:
                issues.append(
                    _err("OP_UNKNOWN_DEVICE", f"op #{i}: device '{dev}' not found.")
                )

        if kind == "set_port_vlan":
            if "port" not in op:
                issues.append(_err("OP_MISSING_FIELD", f"op #{i}: 'port' required."))
            vlan = op.get("vlan")
            if vlan is not None and not _vlan_ok(vlan):
                issues.append(_err("OP_BAD_VLAN", f"op #{i}: bad access VLAN {vlan!r}."))

        if kind == "set_trunk":
            if "port" not in op:
                issues.append(_err("OP_MISSING_FIELD", f"op #{i}: 'port' required."))
            allowed = op.get("allowed_vlans", [])
            if not isinstance(allowed, list) or any(not _vlan_ok(v) for v in allowed):
                issues.append(_err("OP_BAD_VLAN", f"op #{i}: bad trunk allowed_vlans."))
            native = op.get("native_vlan")
            if native is not None and not _vlan_ok(native):
                issues.append(_err("OP_BAD_VLAN", f"op #{i}: bad native VLAN {native!r}."))

        if kind == "add_link":
            for end in ("a_device", "b_device"):
                if op.get(end) not in known_devices:
                    issues.append(
                        _err(
                            "OP_UNKNOWN_DEVICE",
                            f"op #{i}: link endpoint '{op.get(end)}' not found.",
                        )
                    )
            for field in ("a_port", "b_port"):
                if not op.get(field):
                    issues.append(
                        _err("OP_MISSING_FIELD", f"op #{i}: '{field}' required.")
                    )

        if kind == "remove_link" and not op.get("link"):
            issues.append(_err("OP_MISSING_FIELD", f"op #{i}: 'link' required."))

        if kind == "set_subnet":
            if not _vlan_ok(op.get("id")):
                issues.append(_err("OP_BAD_VLAN", f"op #{i}: bad VLAN id for subnet."))
            cidr = op.get("cidr")
            if cidr:
                try:
                    ipaddress.ip_network(cidr, strict=False)
                except (ValueError, TypeError):
                    issues.append(
                        _err("OP_BAD_CIDR", f"op #{i}: '{cidr}' is not a valid CIDR.")
                    )

        if kind in ("add_acl", "remove_acl"):
            if not _vlan_ok(op.get("src_vlan")) or not _vlan_ok(op.get("dst_vlan")):
                issues.append(
                    _err("OP_BAD_VLAN", f"op #{i}: ACL needs int src_vlan/dst_vlan.")
                )
            if op.get("action", "permit") not in ("permit", "deny"):
                issues.append(
                    _err("OP_BAD_ACTION", f"op #{i}: ACL action must be permit/deny.")
                )

        if kind == "rename" and not op.get("name"):
            issues.append(_err("OP_MISSING_FIELD", f"op #{i}: 'name' required."))
    return issues


def destructive_count(ops: list[dict]) -> int:
    return sum(1 for op in ops if op.get("op") in DESTRUCTIVE_OPS)


def ops_to_commands(topology: Topology, ops: list[dict]) -> list[C.Command]:
    """Translate validated ops into reversible Commands."""
    cmds: list[C.Command] = []
    for op in ops:
        kind = op.get("op")
        if kind == "add_device":
            dev = Device(
                id=op.get("id") or new_id(op.get("kind", "dev")[:2]),
                name=op.get("name", "Device"),
                kind=op.get("kind", "switch"),
                x=float(op.get("x", 0.0)),
                y=float(op.get("y", 0.0)),
                ports=[Port(id=p, name=p) for p in op.get("ports", [])],
            )
            cmds.append(C.AddDevice(dev))
        elif kind == "remove_device":
            cmds.append(C.RemoveDevice(op["device"]))
        elif kind == "rename":
            cmds.append(C.RenameDevice(op["device"], op["name"]))
        elif kind == "add_link":
            link = Link(
                id=op.get("id") or new_id("ln"),
                a_device=op["a_device"],
                a_port=op["a_port"],
                b_device=op["b_device"],
                b_port=op["b_port"],
                kind=op.get("kind", "ethernet"),
            )
            cmds.append(C.AddLink(link))
        elif kind == "remove_link":
            cmds.append(C.RemoveLink(op["link"]))
        elif kind == "create_vlan":
            from core.model import Vlan

            cmds.append(
                C.CreateVlan(
                    Vlan(
                        id=op["id"],
                        name=op.get("name", f"VLAN{op['id']}"),
                        color=op.get("color", "#38bdf8"),
                        subnet=op.get("subnet"),
                        gateway=op.get("gateway"),
                    )
                )
            )
        elif kind == "edit_vlan":
            cmds.append(
                C.EditVlan(
                    op["id"],
                    new_name=op.get("name"),
                    color=op.get("color"),
                    subnet=op.get("subnet"),
                    gateway=op.get("gateway"),
                )
            )
        elif kind == "set_device":
            cmds.append(
                C.SetDeviceFields(
                    op["device"],
                    new_name=op.get("name"),
                    mgmt_ip=op.get("mgmt_ip"),
                    mgmt_ip_set="mgmt_ip" in op,
                )
            )
        elif kind == "delete_vlan":
            cmds.append(C.DeleteVlan(op["id"]))
        elif kind == "set_port_vlan":
            cmds.append(C.SetPortVlan(op["device"], op["port"], op.get("vlan")))
        elif kind == "set_trunk":
            cmds.append(
                C.SetTrunk(
                    op["device"],
                    op["port"],
                    list(op.get("allowed_vlans", [])),
                    op.get("native_vlan"),
                )
            )
        elif kind == "set_subnet":
            cmds.append(C.SetSubnet(op["id"], op.get("cidr"), op.get("gateway")))
        elif kind == "add_acl":
            cmds.append(
                C.AddAclRule(
                    AclRule(op["src_vlan"], op["dst_vlan"], op.get("action", "permit"))
                )
            )
        elif kind == "remove_acl":
            cmds.append(
                C.RemoveAclRule(
                    AclRule(op["src_vlan"], op["dst_vlan"], op.get("action", "permit"))
                )
            )
        # unknown ops are dropped here; validate_ops already flagged them
    return cmds
