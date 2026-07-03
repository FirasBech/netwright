"""Netwright topology data model — port-centric, stdlib dataclasses only.

Links join two PORTS, not devices. Ports carry the access/trunk configuration,
which is what real switches do and what validation (native/mode mismatch) and a
future Cisco export need. Every type round-trips through ``to_dict``/``from_dict``;
the whole :class:`Topology` is one JSON document.

VLAN ids are ints in memory but serialize as **string** keys on disk (JSON object
keys are always strings); ``from_dict`` int-coerces them, mirroring
``VlanPolicyEngine._load_policy``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

DEVICE_KINDS = ("switch", "router", "firewall", "host", "server", "ap")
PORT_MODES = ("access", "trunk", "routed", "unused")
LINK_KINDS = ("ethernet", "trunk", "fiber", "wan")
ACL_ACTIONS = ("permit", "deny")

VLAN_MIN = 1
VLAN_MAX = 4094


@dataclass
class Port:
    id: str
    name: str
    mode: str = "access"
    access_vlan: Optional[int] = None
    native_vlan: Optional[int] = None
    allowed_vlans: list[int] = field(default_factory=list)
    ip: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "mode": self.mode,
            "access_vlan": self.access_vlan,
            "native_vlan": self.native_vlan,
            "allowed_vlans": list(self.allowed_vlans),
            "ip": self.ip,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Port":
        return cls(
            id=d["id"],
            name=d.get("name", d["id"]),
            mode=d.get("mode", "access"),
            access_vlan=d.get("access_vlan"),
            native_vlan=d.get("native_vlan"),
            allowed_vlans=[int(v) for v in d.get("allowed_vlans", [])],
            ip=d.get("ip"),
        )


@dataclass
class Device:
    id: str
    name: str
    kind: str
    x: float = 0.0
    y: float = 0.0
    mgmt_ip: Optional[str] = None
    ports: list[Port] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def get_port(self, port_id: str) -> Optional[Port]:
        return next((p for p in self.ports if p.id == port_id), None)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "x": self.x,
            "y": self.y,
            "mgmt_ip": self.mgmt_ip,
            "ports": [p.to_dict() for p in self.ports],
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Device":
        return cls(
            id=d["id"],
            name=d.get("name", d["id"]),
            kind=d.get("kind", "switch"),
            x=float(d.get("x", 0.0)),
            y=float(d.get("y", 0.0)),
            mgmt_ip=d.get("mgmt_ip"),
            ports=[Port.from_dict(p) for p in d.get("ports", [])],
            metadata=dict(d.get("metadata", {})),
        )


@dataclass
class Link:
    id: str
    a_device: str
    a_port: str
    b_device: str
    b_port: str
    kind: str = "ethernet"

    def endpoints(self) -> tuple[tuple[str, str], tuple[str, str]]:
        return ((self.a_device, self.a_port), (self.b_device, self.b_port))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "a_device": self.a_device,
            "a_port": self.a_port,
            "b_device": self.b_device,
            "b_port": self.b_port,
            "kind": self.kind,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Link":
        return cls(
            id=d["id"],
            a_device=d["a_device"],
            a_port=d["a_port"],
            b_device=d["b_device"],
            b_port=d["b_port"],
            kind=d.get("kind", "ethernet"),
        )


@dataclass
class Vlan:
    id: int
    name: str
    color: str = "#38bdf8"
    subnet: Optional[str] = None
    gateway: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "color": self.color,
            "subnet": self.subnet,
            "gateway": self.gateway,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Vlan":
        return cls(
            id=int(d["id"]),
            name=d.get("name", f"VLAN{d['id']}"),
            color=d.get("color", "#38bdf8"),
            subnet=d.get("subnet"),
            gateway=d.get("gateway"),
        )


@dataclass
class Subnet:
    cidr: str
    gateway: Optional[str] = None
    vlan_id: Optional[int] = None

    def to_dict(self) -> dict:
        return {"cidr": self.cidr, "gateway": self.gateway, "vlan_id": self.vlan_id}

    @classmethod
    def from_dict(cls, d: dict) -> "Subnet":
        vid = d.get("vlan_id")
        return cls(
            cidr=d["cidr"],
            gateway=d.get("gateway"),
            vlan_id=int(vid) if vid is not None else None,
        )


@dataclass
class AclRule:
    src_vlan: int
    dst_vlan: int
    action: str = "permit"

    def to_dict(self) -> dict:
        return {
            "src_vlan": self.src_vlan,
            "dst_vlan": self.dst_vlan,
            "action": self.action,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AclRule":
        return cls(
            src_vlan=int(d["src_vlan"]),
            dst_vlan=int(d["dst_vlan"]),
            action=d.get("action", "permit"),
        )


@dataclass
class Topology:
    name: str = "Untitled"
    devices: dict[str, Device] = field(default_factory=dict)
    links: dict[str, Link] = field(default_factory=dict)
    vlans: dict[int, Vlan] = field(default_factory=dict)
    subnets: list[Subnet] = field(default_factory=list)
    acls: list[AclRule] = field(default_factory=list)
    default_action: str = "deny"
    metadata: dict = field(default_factory=dict)

    # ---- mutation helpers (called by Commands, never the UI directly) ------
    def add_device(self, device: Device) -> None:
        self.devices[device.id] = device

    def remove_device(self, device_id: str) -> None:
        self.devices.pop(device_id, None)
        for link_id in [
            lid
            for lid, lk in self.links.items()
            if device_id in (lk.a_device, lk.b_device)
        ]:
            self.links.pop(link_id, None)

    def add_link(self, link: Link) -> None:
        self.links[link.id] = link

    def remove_link(self, link_id: str) -> None:
        self.links.pop(link_id, None)

    def get_port(self, device_id: str, port_id: str) -> Optional[Port]:
        dev = self.devices.get(device_id)
        return dev.get_port(port_id) if dev else None

    def neighbors(self, device_id: str) -> set[str]:
        out: set[str] = set()
        for lk in self.links.values():
            if lk.a_device == device_id:
                out.add(lk.b_device)
            elif lk.b_device == device_id:
                out.add(lk.a_device)
        return out

    def ports_in_vlan(self, vlan_id: int) -> list[tuple[str, str]]:
        result: list[tuple[str, str]] = []
        for dev in self.devices.values():
            for port in dev.ports:
                if port.access_vlan == vlan_id or vlan_id in port.allowed_vlans:
                    result.append((dev.id, port.id))
        return result

    # ---- serialization -----------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "devices": {did: d.to_dict() for did, d in self.devices.items()},
            "links": {lid: lk.to_dict() for lid, lk in self.links.items()},
            # VLAN keys are strings on disk; int in memory.
            "vlans": {str(vid): v.to_dict() for vid, v in self.vlans.items()},
            "subnets": [s.to_dict() for s in self.subnets],
            "acls": [a.to_dict() for a in self.acls],
            "default_action": self.default_action,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Topology":
        return cls(
            name=d.get("name", "Untitled"),
            devices={
                did: Device.from_dict(dv) for did, dv in d.get("devices", {}).items()
            },
            links={lid: Link.from_dict(lk) for lid, lk in d.get("links", {}).items()},
            vlans={
                int(vid): Vlan.from_dict(v) for vid, v in d.get("vlans", {}).items()
            },
            subnets=[Subnet.from_dict(s) for s in d.get("subnets", [])],
            acls=[AclRule.from_dict(a) for a in d.get("acls", [])],
            default_action=d.get("default_action", "deny"),
            metadata=dict(d.get("metadata", {})),
        )
