"""The single mutation funnel: every topology change goes through a Command.

Canvas drags, the properties panel, the CLI, AND the AI all mutate the model
only by executing a :class:`Command` on a :class:`CommandStack`. That makes
undo/redo uniform and AI edits reversible. An AI batch applies as one
:class:`CompositeCommand` so the whole approved proposal is a single undo step.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from .model import AclRule, Device, Link, Subnet, Topology, Vlan


@runtime_checkable
class Command(Protocol):
    name: str

    def do(self, t: Topology) -> None: ...
    def undo(self, t: Topology) -> None: ...


class CommandStack:
    """Linear undo/redo stack with a bounded history."""

    def __init__(self, topology: Topology, limit: int = 200) -> None:
        self.topology = topology
        self.limit = limit
        self._undo: list[Command] = []
        self._redo: list[Command] = []

    def execute(self, cmd: Command) -> None:
        cmd.do(self.topology)
        # Try to merge with the previous command (e.g. a drag = one undo).
        if self._undo:
            prev = self._undo[-1]
            merge = getattr(prev, "merge_with", None)
            if merge is not None and merge(cmd):
                self._redo.clear()
                return
        self._undo.append(cmd)
        if len(self._undo) > self.limit:
            self._undo.pop(0)
        self._redo.clear()

    def undo(self) -> Command | None:
        if not self._undo:
            return None
        cmd = self._undo.pop()
        cmd.undo(self.topology)
        self._redo.append(cmd)
        return cmd

    def redo(self) -> Command | None:
        if not self._redo:
            return None
        cmd = self._redo.pop()
        cmd.do(self.topology)
        self._undo.append(cmd)
        return cmd

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)


# --------------------------------------------------------------------------
# Concrete commands. Each captures enough state to reverse itself exactly.
# --------------------------------------------------------------------------
@dataclass
class AddDevice:
    device: Device
    name: str = "Add device"
    _prev: Device | None = None

    def do(self, t: Topology) -> None:
        # Capture any device this id would displace so undo restores it exactly.
        self._prev = t.devices.get(self.device.id)
        t.add_device(self.device)

    def undo(self, t: Topology) -> None:
        if self._prev is not None:
            t.devices[self.device.id] = self._prev
        else:
            t.remove_device(self.device.id)


@dataclass
class RemoveDevice:
    device_id: str
    name: str = "Remove device"
    _device: Device | None = None
    _links: list[Link] = field(default_factory=list)

    def do(self, t: Topology) -> None:
        self._device = t.devices.get(self.device_id)
        self._links = [
            lk
            for lk in t.links.values()
            if self.device_id in (lk.a_device, lk.b_device)
        ]
        t.remove_device(self.device_id)

    def undo(self, t: Topology) -> None:
        if self._device is not None:
            t.add_device(self._device)
        for lk in self._links:
            t.add_link(lk)


@dataclass
class MoveDevice:
    device_id: str
    x: float
    y: float
    old_x: float | None = None
    old_y: float | None = None
    name: str = "Move device"
    _old: tuple[float, float] | None = None

    def do(self, t: Topology) -> None:
        dev = t.devices.get(self.device_id)
        if dev is None:  # device may have been removed before a deferred commit
            return
        if self._old is None:
            # Prefer explicit old coords (the model may already hold the new
            # position when a live drag is committed on mouse-release).
            if self.old_x is not None and self.old_y is not None:
                self._old = (self.old_x, self.old_y)
            else:
                self._old = (dev.x, dev.y)
        dev.x, dev.y = self.x, self.y

    def undo(self, t: Topology) -> None:
        dev = t.devices.get(self.device_id)
        if dev is not None and self._old is not None:
            dev.x, dev.y = self._old

    def merge_with(self, other: "Command") -> bool:
        """Coalesce consecutive moves of the same device into one undo step."""
        if isinstance(other, MoveDevice) and other.device_id == self.device_id:
            self.x, self.y = other.x, other.y
            return True
        return False


@dataclass
class RenameDevice:
    device_id: str
    new_name: str
    name: str = "Rename device"
    _old: str | None = None

    def do(self, t: Topology) -> None:
        dev = t.devices[self.device_id]
        self._old = dev.name
        dev.name = self.new_name

    def undo(self, t: Topology) -> None:
        if self._old is not None:
            t.devices[self.device_id].name = self._old


@dataclass
class AddLink:
    link: Link
    name: str = "Add link"

    def do(self, t: Topology) -> None:
        t.add_link(self.link)

    def undo(self, t: Topology) -> None:
        t.remove_link(self.link.id)


@dataclass
class RemoveLink:
    link_id: str
    name: str = "Remove link"
    _link: Link | None = None

    def do(self, t: Topology) -> None:
        self._link = t.links.get(self.link_id)
        t.remove_link(self.link_id)

    def undo(self, t: Topology) -> None:
        if self._link is not None:
            t.add_link(self._link)


@dataclass
class SetPortVlan:
    device_id: str
    port_id: str
    access_vlan: int | None
    name: str = "Set port VLAN"
    _old: tuple[str, int | None, list[int]] | None = None

    def do(self, t: Topology) -> None:
        port = t.get_port(self.device_id, self.port_id)
        if port is None:
            return
        self._old = (port.mode, port.access_vlan, list(port.allowed_vlans))
        port.mode = "access"
        port.access_vlan = self.access_vlan
        port.allowed_vlans = []

    def undo(self, t: Topology) -> None:
        port = t.get_port(self.device_id, self.port_id)
        if port is not None and self._old is not None:
            port.mode, port.access_vlan, port.allowed_vlans = self._old


@dataclass
class SetTrunk:
    device_id: str
    port_id: str
    allowed_vlans: list[int]
    native_vlan: int | None = None
    name: str = "Set trunk"
    _old: tuple[str, int | None, int | None, list[int]] | None = None

    def do(self, t: Topology) -> None:
        port = t.get_port(self.device_id, self.port_id)
        if port is None:
            return
        self._old = (
            port.mode,
            port.access_vlan,
            port.native_vlan,
            list(port.allowed_vlans),
        )
        port.mode = "trunk"
        port.access_vlan = None
        port.native_vlan = self.native_vlan
        port.allowed_vlans = list(self.allowed_vlans)

    def undo(self, t: Topology) -> None:
        port = t.get_port(self.device_id, self.port_id)
        if port is not None and self._old is not None:
            port.mode, port.access_vlan, port.native_vlan, port.allowed_vlans = (
                self._old
            )


@dataclass
class CreateVlan:
    vlan: Vlan
    name: str = "Create VLAN"
    _prev: Vlan | None = None

    def do(self, t: Topology) -> None:
        self._prev = t.vlans.get(self.vlan.id)  # restore on undo if displaced
        t.vlans[self.vlan.id] = self.vlan

    def undo(self, t: Topology) -> None:
        if self._prev is not None:
            t.vlans[self.vlan.id] = self._prev
        else:
            t.vlans.pop(self.vlan.id, None)


@dataclass
class EditVlan:
    vlan_id: int
    new_name: str | None = None
    color: str | None = None
    subnet: str | None = None
    gateway: str | None = None
    name: str = "Edit VLAN"
    _old: tuple | None = None

    def do(self, t: Topology) -> None:
        v = t.vlans.get(self.vlan_id)
        if v is None:
            return
        self._old = (v.name, v.color, v.subnet, v.gateway)
        if self.new_name is not None:
            v.name = self.new_name
        if self.color is not None:
            v.color = self.color
        if self.subnet is not None:
            v.subnet = self.subnet
        if self.gateway is not None:
            v.gateway = self.gateway

    def undo(self, t: Topology) -> None:
        v = t.vlans.get(self.vlan_id)
        if v is not None and self._old is not None:
            v.name, v.color, v.subnet, v.gateway = self._old


@dataclass
class SetDeviceFields:
    device_id: str
    new_name: str | None = None
    mgmt_ip: str | None = None
    mgmt_ip_set: bool = False  # allow setting mgmt_ip to None explicitly
    name: str = "Edit device"
    _old: tuple | None = None

    def do(self, t: Topology) -> None:
        d = t.devices.get(self.device_id)
        if d is None:
            return
        self._old = (d.name, d.mgmt_ip)
        if self.new_name is not None:
            d.name = self.new_name
        if self.mgmt_ip_set:
            d.mgmt_ip = self.mgmt_ip

    def undo(self, t: Topology) -> None:
        d = t.devices.get(self.device_id)
        if d is not None and self._old is not None:
            d.name, d.mgmt_ip = self._old


@dataclass
class DeleteVlan:
    vlan_id: int
    name: str = "Delete VLAN"
    _vlan: Vlan | None = None

    def do(self, t: Topology) -> None:
        self._vlan = t.vlans.get(self.vlan_id)
        t.vlans.pop(self.vlan_id, None)

    def undo(self, t: Topology) -> None:
        if self._vlan is not None:
            t.vlans[self.vlan_id] = self._vlan


@dataclass
class SetSubnet:
    vlan_id: int
    cidr: str | None
    gateway: str | None = None
    name: str = "Set subnet"
    _old: tuple[str | None, str | None] | None = None

    def do(self, t: Topology) -> None:
        vlan = t.vlans.get(self.vlan_id)
        if vlan is None:
            return
        self._old = (vlan.subnet, vlan.gateway)
        vlan.subnet = self.cidr
        vlan.gateway = self.gateway

    def undo(self, t: Topology) -> None:
        vlan = t.vlans.get(self.vlan_id)
        if vlan is not None and self._old is not None:
            vlan.subnet, vlan.gateway = self._old


@dataclass
class AddAclRule:
    rule: AclRule
    name: str = "Add ACL rule"

    def do(self, t: Topology) -> None:
        t.acls.append(self.rule)

    def undo(self, t: Topology) -> None:
        if self.rule in t.acls:
            t.acls.remove(self.rule)


@dataclass
class RemoveAclRule:
    rule: AclRule
    name: str = "Remove ACL rule"
    _index: int | None = None

    def do(self, t: Topology) -> None:
        if self.rule in t.acls:
            self._index = t.acls.index(self.rule)
            t.acls.remove(self.rule)

    def undo(self, t: Topology) -> None:
        if self._index is not None:
            t.acls.insert(self._index, self.rule)


@dataclass
class AddSubnet:
    subnet: Subnet
    name: str = "Add subnet"

    def do(self, t: Topology) -> None:
        t.subnets.append(self.subnet)

    def undo(self, t: Topology) -> None:
        if self.subnet in t.subnets:
            t.subnets.remove(self.subnet)


@dataclass
class CompositeCommand:
    """Apply a list of commands as one undoable unit (used for AI batches)."""

    commands: list[Command]
    name: str = "Apply changes"

    def do(self, t: Topology) -> None:
        for cmd in self.commands:
            cmd.do(t)

    def undo(self, t: Topology) -> None:
        for cmd in reversed(self.commands):
            cmd.undo(t)


def snapshot(topology: Topology) -> dict:
    """Deep, comparable snapshot of a topology (for tests / equality checks)."""
    return copy.deepcopy(topology.to_dict())
