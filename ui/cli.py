"""Netwright command-line interface.

Mirrors SecureLink's argparse style: a ``common`` parent parser carries the
``--project PATH`` isolation flag (the analogue of SecureLink's ``--state-dir``),
every ``_cmd_*`` returns an int, output is ``json.dumps(indent=2)``, and a
validation error or a no-key propose exits non-zero. Mutating subcommands load
the project, run Commands through a CommandStack, and save — the same mutation
path as the GUI.
"""
from __future__ import annotations

import argparse
import json
import sys

from ai.assistant import explain, propose_change
from ai.client import NetwrightAI
from ai.tools import ops_to_commands
from core.commands import (
    AddAclRule,
    AddDevice,
    CommandStack,
    CompositeCommand,
    CreateVlan,
    DeleteVlan,
    SetPortVlan,
)
from core.discovery import discover_from_files
from core.export import export_device_cli, export_json, export_policy, export_svg
from core.live_discovery import available as ssh_available
from core.live_discovery import live_discover, load_inventory
from core.snmp_discovery import available as snmp_available
from core.snmp_discovery import load_inventory as load_snmp_inventory
from core.snmp_discovery import snmp_discover
from core.ids import new_id
from core.model import AclRule, Device, Link, Port, Topology, Vlan
from core.policy import import_policy_file
from core.project import NetwrightProject, ProjectError
from core.reachability import trace
from core.validate import validate


def _load(path: str) -> NetwrightProject:
    return NetwrightProject.load(path)


def _emit(obj) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True))


# ---- commands -------------------------------------------------------------
def _cmd_new(args) -> int:
    project = NetwrightProject(name=args.name, topology=Topology(args.name))
    project.save(args.project)
    _emit({"created": args.project, "name": args.name})
    return 0


def _cmd_vlan(args) -> int:
    project = _load(args.project)
    stack = CommandStack(project.topology)
    if args.vlan_cmd == "create":
        stack.execute(CreateVlan(Vlan(id=args.id, name=args.vlan_name)))
    elif args.vlan_cmd == "delete":
        stack.execute(DeleteVlan(args.id))
    elif args.vlan_cmd == "list":
        _emit(
            [v.to_dict() for v in sorted(project.topology.vlans.values(), key=lambda v: v.id)]
        )
        return 0
    project.save(args.project)
    _emit({"ok": True, "vlans": sorted(project.topology.vlans)})
    return 0


def _cmd_add_device(args) -> int:
    project = _load(args.project)
    stack = CommandStack(project.topology)
    dev = Device(
        id=args.id or new_id(args.kind[:2]),
        name=args.name,
        kind=args.kind,
        ports=[Port(id=f"Gi0/{i}", name=f"Gi0/{i}") for i in range(1, args.ports + 1)],
    )
    stack.execute(AddDevice(dev))
    project.save(args.project)
    _emit({"added": dev.id, "kind": dev.kind})
    return 0


def _cmd_set_port(args) -> int:
    project = _load(args.project)
    stack = CommandStack(project.topology)
    stack.execute(SetPortVlan(args.device, args.port, args.access_vlan))
    project.save(args.project)
    _emit({"ok": True})
    return 0


def _cmd_acl(args) -> int:
    project = _load(args.project)
    stack = CommandStack(project.topology)
    stack.execute(AddAclRule(AclRule(args.src, args.dst, "permit")))
    project.save(args.project)
    _emit({"ok": True})
    return 0


def _cmd_link(args) -> int:
    project = _load(args.project)
    a_dev, a_port = args.a.split(":")
    b_dev, b_port = args.b.split(":")
    link = Link(new_id("ln"), a_dev, a_port, b_dev, b_port, kind=args.kind)
    from core.commands import AddLink

    CommandStack(project.topology).execute(AddLink(link))
    project.save(args.project)
    _emit({"added": link.id})
    return 0


def _cmd_validate(args) -> int:
    project = _load(args.project)
    issues = validate(project.topology)
    _emit([i.to_dict() for i in issues])
    return 1 if any(i.severity == "error" for i in issues) else 0


def _cmd_export(args) -> int:
    project = _load(args.project)
    fmt = args.format
    if fmt == "json":
        export_json(project.topology, args.out)
    elif fmt == "policy":
        export_policy(project.topology, args.out)
    elif fmt == "svg":
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(export_svg(project.topology))
    elif fmt == "ios":
        if not args.device:
            print("--device is required for IOS export.", file=sys.stderr)
            return 1
        try:
            cfg = export_device_cli(project.topology, args.device)
        except KeyError:
            print(f"error: device '{args.device}' not found.", file=sys.stderr)
            return 1
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(cfg)
    elif fmt == "png":
        print("PNG export needs the dashboard (a rendered scene).", file=sys.stderr)
        return 1
    _emit({"exported": args.out, "format": fmt})
    return 0


def _cmd_import_policy(args) -> int:
    project = _load(args.project)
    acls = import_policy_file(args.file)
    existing = {(a.src_vlan, a.dst_vlan, a.action) for a in project.topology.acls}
    added = 0
    stack = CommandStack(project.topology)
    for acl in acls:
        if (acl.src_vlan, acl.dst_vlan, acl.action) not in existing:
            stack.execute(AddAclRule(acl))
            added += 1
    project.save(args.project)
    _emit({"imported": added})
    return 0


def _cmd_discover(args) -> int:
    topology = discover_from_files(args.files, fmt=args.format, name=args.name)
    project = NetwrightProject(name=args.name, topology=topology)
    project.save(args.project)
    _emit(
        {
            "discovered": args.project,
            "devices": len(topology.devices),
            "links": len(topology.links),
            "roles": sorted({d.kind for d in topology.devices.values()}),
        }
    )
    return 0


def _cmd_live_discover(args) -> int:
    if not args.authorized:
        print(
            "Refusing to connect: pass --authorized to confirm you may manage "
            "these devices.",
            file=sys.stderr,
        )
        return 1
    if not ssh_available():
        print("netmiko is not installed. Run: pip install netmiko", file=sys.stderr)
        return 1
    creds = load_inventory(args.inventory)
    result = live_discover(creds, name=args.name, authorized=True)
    project = NetwrightProject(name=args.name, topology=result.topology)
    project.save(args.project)
    _emit(
        {
            "discovered": args.project,
            "reached": result.reached,
            "unreachable": result.errors,
            "devices": len(result.topology.devices),
            "links": len(result.topology.links),
        }
    )
    return 0 if result.reached else 1


def _cmd_snmp_discover(args) -> int:
    if not args.authorized:
        print(
            "Refusing to query: pass --authorized to confirm you may query "
            "these devices.",
            file=sys.stderr,
        )
        return 1
    if not snmp_available():
        print("pysnmp is not installed. Run: pip install pysnmp", file=sys.stderr)
        return 1
    creds = load_snmp_inventory(args.inventory)
    result = snmp_discover(creds, name=args.name, authorized=True)
    project = NetwrightProject(name=args.name, topology=result.topology)
    project.save(args.project)
    _emit(
        {
            "discovered": args.project,
            "reached": result.reached,
            "unreachable": result.errors,
            "devices": len(result.topology.devices),
            "links": len(result.topology.links),
        }
    )
    return 0 if result.reached else 1


def _cmd_reach(args) -> int:
    project = _load(args.project)
    result = trace(project.topology, args.src, args.dst)
    _emit(
        {
            "reachable": result.reachable,
            "reason": result.reason,
            "src_vlan": result.src_vlan,
            "dst_vlan": result.dst_vlan,
            "path": list(result.path),
        }
    )
    return 0 if result.reachable else 2


def _cmd_explain(args) -> int:
    project = _load(args.project)
    ai = NetwrightAI()
    print(explain(project.topology, ai if ai.available() else None))
    return 0


def _cmd_propose(args) -> int:
    project = _load(args.project)
    ai = NetwrightAI()
    if not ai.available():
        print("Set ANTHROPIC_API_KEY to use the assistant.", file=sys.stderr)
        return 1
    change = propose_change(project.topology, args.intent, ai)
    _emit(
        {
            "summary": change.summary,
            "ops": change.ops,
            "applicable": change.applicable,
            "errors": [i.to_dict() for i in change.op_issues if i.severity == "error"],
        }
    )
    if args.apply and change.applicable:
        stack = CommandStack(project.topology)
        stack.execute(
            CompositeCommand(ops_to_commands(project.topology, change.ops))
        )
        project.save(args.project)
    return 0 if change.applicable else 1


# ---- parser ---------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--project",
        default="project.netwright",
        help="Path to the .netwright project file (default: project.netwright).",
    )

    parser = argparse.ArgumentParser(prog="netwright", description="Netwright CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_new = sub.add_parser("new", parents=[common], help="Create a new project")
    p_new.add_argument("--name", default="Untitled")
    p_new.set_defaults(func=_cmd_new)

    p_dev = sub.add_parser("add-device", parents=[common], help="Add a device")
    p_dev.add_argument("--kind", default="switch")
    p_dev.add_argument("--name", default="Device")
    p_dev.add_argument("--id", default=None)
    p_dev.add_argument("--ports", type=int, default=4)
    p_dev.set_defaults(func=_cmd_add_device)

    p_vlan = sub.add_parser("vlan", parents=[common], help="VLAN operations")
    vsub = p_vlan.add_subparsers(dest="vlan_cmd", required=True)
    v_create = vsub.add_parser("create", parents=[common])
    v_create.add_argument("--id", type=int, required=True)
    v_create.add_argument("--vlan-name", dest="vlan_name", default="VLAN")
    vsub.add_parser("list", parents=[common])
    v_del = vsub.add_parser("delete", parents=[common])
    v_del.add_argument("--id", type=int, required=True)
    p_vlan.set_defaults(func=_cmd_vlan)

    p_link = sub.add_parser("link", parents=[common], help="Link two ports")
    p_link.add_argument("--a", required=True, help="device:port")
    p_link.add_argument("--b", required=True, help="device:port")
    p_link.add_argument("--kind", default="ethernet")
    p_link.set_defaults(func=_cmd_link)

    p_port = sub.add_parser("set-port", parents=[common], help="Set access VLAN")
    p_port.add_argument("--device", required=True)
    p_port.add_argument("--port", required=True)
    p_port.add_argument("--access-vlan", dest="access_vlan", type=int, required=True)
    p_port.set_defaults(func=_cmd_set_port)

    p_acl = sub.add_parser("acl", parents=[common], help="ACL operations")
    asub = p_acl.add_subparsers(dest="acl_cmd", required=True)
    a_add = asub.add_parser("add", parents=[common])
    a_add.add_argument("--src", type=int, required=True)
    a_add.add_argument("--dst", type=int, required=True)
    p_acl.set_defaults(func=_cmd_acl)

    p_val = sub.add_parser("validate", parents=[common], help="Validate the design")
    p_val.set_defaults(func=_cmd_validate)

    p_exp = sub.add_parser("export", parents=[common], help="Export artifacts")
    p_exp.add_argument(
        "--format", choices=["json", "svg", "png", "policy", "ios"], required=True
    )
    p_exp.add_argument("--out", required=True)
    p_exp.add_argument("--device", default=None, help="device id (for --format ios)")
    p_exp.set_defaults(func=_cmd_export)

    p_imp = sub.add_parser(
        "import-policy", parents=[common], help="Import a SecureLink vlan_policy.json"
    )
    p_imp.add_argument("--file", required=True)
    p_imp.set_defaults(func=_cmd_import_policy)

    p_disc = sub.add_parser(
        "discover", parents=[common],
        help="Build a topology from LLDP/CDP neighbor output files",
    )
    p_disc.add_argument("files", nargs="+", help="neighbor dump file(s), one per device")
    p_disc.add_argument(
        "--format", default="auto",
        choices=["auto", "cdp", "huawei_lldp", "huawei_brief", "lldp_std"],
    )
    p_disc.add_argument("--name", default="Discovered")
    p_disc.set_defaults(func=_cmd_discover)

    p_live = sub.add_parser(
        "live-discover", parents=[common],
        help="Discover over SSH from a device inventory (authorized use only)",
    )
    p_live.add_argument(
        "--inventory", required=True,
        help="JSON list of {host, username, password?, device_type?}",
    )
    p_live.add_argument("--name", default="Discovered")
    p_live.add_argument(
        "--authorized", action="store_true",
        help="confirm you are authorized to connect to these devices",
    )
    p_live.set_defaults(func=_cmd_live_discover)

    p_snmp = sub.add_parser(
        "snmp-discover", parents=[common],
        help="Discover over SNMP (LLDP-MIB) from an inventory (authorized use only)",
    )
    p_snmp.add_argument(
        "--inventory", required=True,
        help="JSON list of {host, community?, version?, port?}",
    )
    p_snmp.add_argument("--name", default="Discovered")
    p_snmp.add_argument(
        "--authorized", action="store_true",
        help="confirm you are authorized to query these devices",
    )
    p_snmp.set_defaults(func=_cmd_snmp_discover)

    p_reach = sub.add_parser(
        "reach", parents=[common], help="Static reachability between two devices"
    )
    p_reach.add_argument("--src", required=True)
    p_reach.add_argument("--dst", required=True)
    p_reach.set_defaults(func=_cmd_reach)

    p_explain = sub.add_parser("explain", parents=[common], help="Explain the design")
    p_explain.set_defaults(func=_cmd_explain)

    p_prop = sub.add_parser("propose", parents=[common], help="AI: propose changes")
    p_prop.add_argument("--intent", required=True)
    p_prop.add_argument("--apply", action="store_true")
    p_prop.set_defaults(func=_cmd_propose)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ProjectError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
