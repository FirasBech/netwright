"""Deterministic, UI-free, AI-free validation engine.

``validate(topology)`` is a pure function returning a list of :class:`Issue`.
It is the single source of truth for correctness: the dashboard Issues panel,
the CLI ``validate`` command, and the AI "validate" flow all call this same
function, so humans and the model see identical results.

All subnet math uses the stdlib ``ipaddress`` module. /31 (RFC 3021) and /32
edge cases are handled so they do not false-positive.
"""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field

from .model import VLAN_MAX, VLAN_MIN, Topology
from .policy import VlanPolicyEngine


@dataclass(frozen=True)
class Issue:
    severity: str  # 'error' | 'warning' | 'info'
    code: str
    message: str
    device_ids: tuple[str, ...] = ()
    link_ids: tuple[str, ...] = ()
    vlan_ids: tuple[int, ...] = ()
    fix_hint: str = ""

    def to_dict(self) -> dict:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "device_ids": list(self.device_ids),
            "link_ids": list(self.link_ids),
            "vlan_ids": list(self.vlan_ids),
            "fix_hint": self.fix_hint,
        }


def _vlan_in_range(vid: int) -> bool:
    return VLAN_MIN <= vid <= VLAN_MAX


def validate(
    topology: Topology, policy: VlanPolicyEngine | None = None
) -> list[Issue]:
    issues: list[Issue] = []
    defined = set(topology.vlans.keys())

    issues += _check_vlan_ranges(topology)
    issues += _check_vlan1_in_use(topology)
    issues += _check_dup_vlan_id(topology)
    issues += _check_port_vlan_refs(topology, defined)
    issues += _check_links(topology)
    issues += _check_subnets(topology)
    issues += _check_dup_ip(topology)
    issues += _check_isolated_devices(topology)
    issues += _check_acls(topology, defined, policy)
    return issues


def _referenced_vlan_ids(topology: Topology) -> set[int]:
    refs: set[int] = set(topology.vlans.keys())
    for dev in topology.devices.values():
        for port in dev.ports:
            if port.access_vlan is not None:
                refs.add(port.access_vlan)
            if port.native_vlan is not None:
                refs.add(port.native_vlan)
            refs.update(port.allowed_vlans)
    for acl in topology.acls:
        refs.add(acl.src_vlan)
        refs.add(acl.dst_vlan)
    for sn in topology.subnets:
        if sn.vlan_id is not None:
            refs.add(sn.vlan_id)
    return refs


def _check_vlan_ranges(topology: Topology) -> list[Issue]:
    out: list[Issue] = []
    for vid in sorted(_referenced_vlan_ids(topology)):
        if not _vlan_in_range(vid):
            out.append(
                Issue(
                    "error",
                    "VLAN_OUT_OF_RANGE",
                    f"VLAN {vid} is outside the valid range {VLAN_MIN}-{VLAN_MAX} "
                    f"(0 and 4095 are reserved).",
                    vlan_ids=(vid,),
                    fix_hint=f"Use a VLAN id between {VLAN_MIN} and {VLAN_MAX}.",
                )
            )
    return out


def _check_vlan1_in_use(topology: Topology) -> list[Issue]:
    for dev in topology.devices.values():
        for port in dev.ports:
            if port.access_vlan == 1 or port.native_vlan == 1:
                return [
                    Issue(
                        "warning",
                        "VLAN1_IN_USE",
                        "VLAN 1 is used as a data/native VLAN; best practice is to "
                        "move user traffic off the default VLAN.",
                        device_ids=(dev.id,),
                        vlan_ids=(1,),
                        fix_hint="Reassign the port to a dedicated VLAN.",
                    )
                ]
    return []


def _check_dup_vlan_id(topology: Topology) -> list[Issue]:
    out: list[Issue] = []
    seen: dict[int, list] = {}
    for v in topology.vlans.values():
        seen.setdefault(v.id, []).append(v)
    for vid, defs in seen.items():
        if len(defs) > 1:
            out.append(
                Issue(
                    "error",
                    "DUP_VLAN_ID",
                    f"VLAN id {vid} is defined {len(defs)} times with conflicting "
                    f"settings.",
                    vlan_ids=(vid,),
                    fix_hint="Merge the duplicate VLAN definitions.",
                )
            )
    return out


def _check_port_vlan_refs(topology: Topology, defined: set[int]) -> list[Issue]:
    out: list[Issue] = []
    for dev in topology.devices.values():
        for port in dev.ports:
            if (
                port.mode == "access"
                and port.access_vlan is not None
                and _vlan_in_range(port.access_vlan)
                and port.access_vlan not in defined
            ):
                out.append(
                    Issue(
                        "error",
                        "ACCESS_VLAN_UNDEFINED",
                        f"Port {dev.name}/{port.name} is an access port on VLAN "
                        f"{port.access_vlan}, which is not defined.",
                        device_ids=(dev.id,),
                        vlan_ids=(port.access_vlan,),
                        fix_hint="Create the VLAN or pick a defined one.",
                    )
                )
            if port.mode == "trunk":
                for vid in port.allowed_vlans:
                    if _vlan_in_range(vid) and vid not in defined:
                        out.append(
                            Issue(
                                "error",
                                "TRUNK_VLAN_UNDEFINED",
                                f"Trunk {dev.name}/{port.name} allows VLAN {vid}, "
                                f"which is not defined.",
                                device_ids=(dev.id,),
                                vlan_ids=(vid,),
                                fix_hint="Create the VLAN or remove it from the "
                                "trunk allowed list.",
                            )
                        )
    return out


def _check_links(topology: Topology) -> list[Issue]:
    out: list[Issue] = []
    port_use: dict[tuple[str, str], list[str]] = {}

    for lk in topology.links.values():
        a_port = topology.get_port(lk.a_device, lk.a_port)
        b_port = topology.get_port(lk.b_device, lk.b_port)

        if (
            lk.a_device not in topology.devices
            or lk.b_device not in topology.devices
            or a_port is None
            or b_port is None
        ):
            out.append(
                Issue(
                    "error",
                    "DANGLING_LINK",
                    f"Link {lk.id} references a missing device or port.",
                    link_ids=(lk.id,),
                    fix_hint="Delete the link or fix its endpoints.",
                )
            )
            continue

        port_use.setdefault((lk.a_device, lk.a_port), []).append(lk.id)
        port_use.setdefault((lk.b_device, lk.b_port), []).append(lk.id)

        if {a_port.mode, b_port.mode} == {"access", "trunk"}:
            out.append(
                Issue(
                    "error",
                    "MODE_MISMATCH",
                    f"Link {lk.id} joins an access port to a trunk port.",
                    link_ids=(lk.id,),
                    device_ids=(lk.a_device, lk.b_device),
                    fix_hint="Make both ends access or both ends trunk.",
                )
            )

        if (
            a_port.mode == "trunk"
            and b_port.mode == "trunk"
            and a_port.native_vlan is not None
            and b_port.native_vlan is not None
            and a_port.native_vlan != b_port.native_vlan
        ):
            out.append(
                Issue(
                    "warning",
                    "NATIVE_VLAN_MISMATCH",
                    f"Trunk link {lk.id} has mismatched native VLANs "
                    f"({a_port.native_vlan} vs {b_port.native_vlan}).",
                    link_ids=(lk.id,),
                    device_ids=(lk.a_device, lk.b_device),
                    fix_hint="Set the same native VLAN on both trunk ends.",
                )
            )

    for (dev_id, port_id), link_ids in port_use.items():
        if len(link_ids) > 1:
            out.append(
                Issue(
                    "error",
                    "PORT_DOUBLE_LINKED",
                    f"Port {dev_id}/{port_id} is used by {len(link_ids)} links.",
                    device_ids=(dev_id,),
                    link_ids=tuple(link_ids),
                    fix_hint="A physical port can carry only one link.",
                )
            )
    return out


def _network(cidr: str):
    """Return (network, host_bits_set?). Raises ValueError on a bad CIDR."""
    strict = ipaddress.ip_network(cidr, strict=False)
    try:
        ipaddress.ip_network(cidr, strict=True)
        host_bits = False
    except ValueError:
        host_bits = True
    return strict, host_bits


def _check_subnets(topology: Topology) -> list[Issue]:
    out: list[Issue] = []
    networks: list[tuple[int, ipaddress._BaseNetwork]] = []

    for vlan in topology.vlans.values():
        if not vlan.subnet:
            continue
        try:
            net, host_bits = _network(vlan.subnet)
        except ValueError:
            out.append(
                Issue(
                    "error",
                    "HOST_BITS_SET",
                    f"VLAN {vlan.id} subnet '{vlan.subnet}' is not a valid CIDR.",
                    vlan_ids=(vlan.id,),
                    fix_hint="Use a valid network/prefix, e.g. 10.0.10.0/24.",
                )
            )
            continue
        if host_bits:
            out.append(
                Issue(
                    "error",
                    "HOST_BITS_SET",
                    f"VLAN {vlan.id} subnet '{vlan.subnet}' has host bits set; "
                    f"the network address is {net.with_prefixlen}.",
                    vlan_ids=(vlan.id,),
                    fix_hint=f"Use {net.with_prefixlen}.",
                )
            )
        networks.append((vlan.id, net))

        if vlan.gateway:
            out += _check_gateway(vlan.id, vlan.gateway, net)

    for i in range(len(networks)):
        for j in range(i + 1, len(networks)):
            vid_a, net_a = networks[i]
            vid_b, net_b = networks[j]
            if net_a.version == net_b.version and net_a.overlaps(net_b):
                out.append(
                    Issue(
                        "error",
                        "SUBNET_OVERLAP",
                        f"VLAN {vid_a} ({net_a.with_prefixlen}) overlaps VLAN "
                        f"{vid_b} ({net_b.with_prefixlen}).",
                        vlan_ids=(vid_a, vid_b),
                        fix_hint="Re-address one of the subnets.",
                    )
                )
    return out


def _check_gateway(vlan_id: int, gateway: str, net) -> list[Issue]:
    try:
        gw = ipaddress.ip_address(gateway)
    except ValueError:
        return [
            Issue(
                "error",
                "GATEWAY_OUTSIDE_SUBNET",
                f"VLAN {vlan_id} gateway '{gateway}' is not a valid IP address.",
                vlan_ids=(vlan_id,),
            )
        ]
    if gw not in net:
        return [
            Issue(
                "error",
                "GATEWAY_OUTSIDE_SUBNET",
                f"VLAN {vlan_id} gateway {gateway} is not inside {net.with_prefixlen}.",
                vlan_ids=(vlan_id,),
                fix_hint="Pick a gateway address within the subnet.",
            )
        ]
    # /31 and /32 have no usable network/broadcast distinction; skip that check.
    if net.prefixlen <= 30 and gw in (net.network_address, net.broadcast_address):
        return [
            Issue(
                "error",
                "GATEWAY_OUTSIDE_SUBNET",
                f"VLAN {vlan_id} gateway {gateway} is the network or broadcast "
                f"address of {net.with_prefixlen}.",
                vlan_ids=(vlan_id,),
                fix_hint="Use a host address inside the subnet.",
            )
        ]
    return []


def _check_dup_ip(topology: Topology) -> list[Issue]:
    out: list[Issue] = []
    # ip -> list of device ids holding it (across ALL interfaces, incl. same dev)
    holders: dict[str, list[str]] = {}
    for dev in topology.devices.values():
        addrs = []
        if dev.mgmt_ip:
            addrs.append(dev.mgmt_ip)
        addrs += [p.ip for p in dev.ports if p.ip]
        for ip in addrs:
            holders.setdefault(ip, []).append(dev.id)
    for ip, devs in holders.items():
        if len(devs) > 1:  # duplicate across OR within a device
            out.append(
                Issue(
                    "error",
                    "DUP_IP",
                    f"IP {ip} is assigned to {len(devs)} interfaces.",
                    device_ids=tuple(dict.fromkeys(devs)),
                    fix_hint="Give each interface a unique address.",
                )
            )
    return out


def _check_isolated_devices(topology: Topology) -> list[Issue]:
    out: list[Issue] = []
    linked: set[str] = set()
    for lk in topology.links.values():
        linked.add(lk.a_device)
        linked.add(lk.b_device)
    for dev in topology.devices.values():
        if dev.id not in linked:
            out.append(
                Issue(
                    "warning",
                    "ISOLATED_DEVICE",
                    f"Device {dev.name} has no links.",
                    device_ids=(dev.id,),
                    fix_hint="Connect it, or remove it if unused.",
                )
            )
    return out


def _check_acls(
    topology: Topology, defined: set[int], policy: VlanPolicyEngine | None
) -> list[Issue]:
    out: list[Issue] = []
    pairs: dict[tuple[int, int], set[str]] = {}

    for acl in topology.acls:
        for vid in (acl.src_vlan, acl.dst_vlan):
            if _vlan_in_range(vid) and vid not in defined:
                out.append(
                    Issue(
                        "error",
                        "ACL_REFERENCES_UNKNOWN_VLAN",
                        f"ACL rule references undefined VLAN {vid}.",
                        vlan_ids=(vid,),
                        fix_hint="Define the VLAN or drop the rule.",
                    )
                )
        pairs.setdefault((acl.src_vlan, acl.dst_vlan), set()).add(acl.action)

    for (src, dst), actions in pairs.items():
        if "permit" in actions and "deny" in actions:
            out.append(
                Issue(
                    "warning",
                    "ACL_CONTRADICTION",
                    f"VLAN {src} -> {dst} has both a permit and a deny rule.",
                    vlan_ids=(src, dst),
                    fix_hint="Keep one rule for the pair.",
                )
            )

    if policy is not None:
        for acl in topology.acls:
            if acl.action == "permit" and not policy.is_allowed(
                acl.src_vlan, acl.dst_vlan
            ):
                out.append(
                    Issue(
                        "info",
                        "INTER_VLAN_DENIED",
                        f"VLAN {acl.src_vlan} -> {acl.dst_vlan} is permitted in the "
                        f"design but denied by the deployed policy map.",
                        vlan_ids=(acl.src_vlan, acl.dst_vlan),
                        fix_hint="Update vlan_policy.json to match the design.",
                    )
                )
    return out
