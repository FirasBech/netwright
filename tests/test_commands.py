from core.commands import (
    AddDevice,
    CommandStack,
    CompositeCommand,
    CreateVlan,
    MoveDevice,
    RemoveDevice,
    snapshot,
)
from core.model import Device, Topology, Vlan


def test_do_undo_redo():
    t = Topology()
    stack = CommandStack(t)
    stack.execute(AddDevice(Device("d1", "D1", "switch")))
    assert "d1" in t.devices
    stack.undo()
    assert "d1" not in t.devices
    stack.redo()
    assert "d1" in t.devices


def test_move_device_coalesces_into_one_undo():
    t = Topology(devices={"d1": Device("d1", "D1", "switch", x=0, y=0)})
    stack = CommandStack(t)
    stack.execute(MoveDevice("d1", 10, 0))
    stack.execute(MoveDevice("d1", 20, 0))
    stack.execute(MoveDevice("d1", 30, 0))
    assert t.devices["d1"].x == 30
    stack.undo()  # one undo reverts the whole drag back to the start
    assert t.devices["d1"].x == 0
    assert stack.can_undo() is False


def test_remove_device_restores_links(sample_topology):
    before = snapshot(sample_topology)
    stack = CommandStack(sample_topology)
    stack.execute(RemoveDevice("sw1"))
    assert "ln1" not in sample_topology.links  # incident link removed
    stack.undo()
    assert snapshot(sample_topology) == before  # device AND link restored


def test_composite_batch_is_one_undo():
    t = Topology()
    before = snapshot(t)
    stack = CommandStack(t)
    batch = CompositeCommand(
        [
            CreateVlan(Vlan(40, "Guest")),
            AddDevice(Device("d1", "D1", "switch")),
        ]
    )
    stack.execute(batch)
    assert 40 in t.vlans and "d1" in t.devices
    stack.undo()
    assert snapshot(t) == before
