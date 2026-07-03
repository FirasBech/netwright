"""Static reachability tracer — "can host A reach host B?" without simulation.

This is **static analysis**, not a packet simulator: it answers L2 reachability
by walking the VLAN-carrying link graph, and inter-VLAN reachability by checking
the deny-by-default ACL/policy intent. It does not model routing tables, STP
convergence, or live forwarding — see Known Limitations.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from .model import Topology
from .policy import VlanPolicyEngine


@dataclass(frozen=True)
class Reach:
    reachable: bool
    reason: str
    src_vlan: int | None = None
    dst_vlan: int | None = None
    path: tuple[str, ...] = field(default_factory=tuple)


def device_access_vlan(topology: Topology, device_id: str) -> int | None:
    """The access VLAN of a device's first access port (its 'home' VLAN)."""
    dev = topology.devices.get(device_id)
    if not dev:
        return None
    for port in dev.ports:
        if port.mode == "access" and port.access_vlan is not None:
            return port.access_vlan
    return None


def _port_carries(port, vlan: int) -> bool:
    if port.mode == "access":
        return port.access_vlan == vlan
    if port.mode == "trunk":
        return vlan in port.allowed_vlans or port.native_vlan == vlan
    return False


def _link_carries(topology: Topology, link, vlan: int) -> bool:
    a = topology.get_port(link.a_device, link.a_port)
    b = topology.get_port(link.b_device, link.b_port)
    return bool(a and b and _port_carries(a, vlan) and _port_carries(b, vlan))


def l2_path(topology: Topology, src: str, dst: str, vlan: int) -> list[str]:
    """BFS over links that carry ``vlan`` on both ends. [] if unreachable."""
    if src == dst:
        return [src]
    prev: dict[str, str] = {src: src}
    queue: deque[str] = deque([src])
    while queue:
        node = queue.popleft()
        for link in topology.links.values():
            if not _link_carries(topology, link, vlan):
                continue
            nxt = None
            if link.a_device == node:
                nxt = link.b_device
            elif link.b_device == node:
                nxt = link.a_device
            if nxt is not None and nxt not in prev:
                prev[nxt] = node
                if nxt == dst:
                    return _reconstruct(prev, src, dst)
                queue.append(nxt)
    return []


def _reconstruct(prev: dict[str, str], src: str, dst: str) -> list[str]:
    path = [dst]
    while path[-1] != src:
        path.append(prev[path[-1]])
    path.reverse()
    return path


def _permits(topology: Topology, src_vlan: int, dst_vlan: int,
             policy: VlanPolicyEngine | None) -> bool:
    for acl in topology.acls:
        if acl.src_vlan == src_vlan and acl.dst_vlan == dst_vlan:
            return acl.action == "permit"
    if policy is not None:
        return policy.is_allowed(src_vlan, dst_vlan)
    return False  # deny-by-default


def trace(topology: Topology, src: str, dst: str,
          policy: VlanPolicyEngine | None = None) -> Reach:
    """Decide whether ``src`` can reach ``dst`` under the current design."""
    src_vlan = device_access_vlan(topology, src)
    dst_vlan = device_access_vlan(topology, dst)
    if src_vlan is None or dst_vlan is None:
        return Reach(False, "One or both endpoints have no access VLAN.",
                     src_vlan, dst_vlan)

    if src_vlan == dst_vlan:
        path = l2_path(topology, src, dst, src_vlan)
        if path:
            return Reach(True, f"Same VLAN {src_vlan}; L2 path exists.",
                         src_vlan, dst_vlan, tuple(path))
        return Reach(False, f"Same VLAN {src_vlan} but no L2 path carries it.",
                     src_vlan, dst_vlan)

    if _permits(topology, src_vlan, dst_vlan, policy):
        return Reach(True,
                     f"Inter-VLAN {src_vlan}->{dst_vlan} permitted (needs routing).",
                     src_vlan, dst_vlan)
    return Reach(False,
                 f"Inter-VLAN {src_vlan}->{dst_vlan} denied by deny-by-default policy.",
                 src_vlan, dst_vlan)
