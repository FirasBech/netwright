"""Regression tests locking in the adversarial-review fixes."""
import json

import pytest

from core.commands import (
    AddDevice,
    CommandStack,
    CreateVlan,
    EditVlan,
    MoveDevice,
    SetDeviceFields,
)
from core.model import Device, Port, Topology, Vlan
from core.validate import validate


# [16] CreateVlan.undo must restore a displaced VLAN, not delete the id.
def test_create_vlan_undo_restores_displaced():
    t = Topology(vlans={10: Vlan(10, "Original")})
    stack = CommandStack(t)
    stack.execute(CreateVlan(Vlan(10, "Replacement")))
    assert t.vlans[10].name == "Replacement"
    stack.undo()
    assert t.vlans[10].name == "Original"  # not deleted


# [17] AddDevice.undo must not destroy a pre-existing same-id device.
def test_add_device_undo_restores_displaced():
    orig = Device("d1", "Original", "switch")
    t = Topology(devices={"d1": orig})
    stack = CommandStack(t)
    stack.execute(AddDevice(Device("d1", "New", "router")))
    assert t.devices["d1"].name == "New"
    stack.undo()
    assert t.devices["d1"].name == "Original"


# [1] MoveDevice must not raise if the device is gone by commit time.
def test_move_device_missing_is_noop():
    t = Topology()
    cmd = MoveDevice("ghost", 10, 10, old_x=0, old_y=0)
    cmd.do(t)   # must not raise
    cmd.undo(t)  # must not raise


# EditVlan / SetDeviceFields round-trip cleanly.
def test_edit_vlan_undoable():
    t = Topology(vlans={10: Vlan(10, "Sales", subnet=None)})
    stack = CommandStack(t)
    stack.execute(EditVlan(10, new_name="Marketing", subnet="10.0.10.0/24"))
    assert t.vlans[10].name == "Marketing"
    assert t.vlans[10].subnet == "10.0.10.0/24"
    stack.undo()
    assert t.vlans[10].name == "Sales"
    assert t.vlans[10].subnet is None


def test_set_device_fields_undoable():
    t = Topology(devices={"d1": Device("d1", "D1", "switch", mgmt_ip=None)})
    stack = CommandStack(t)
    stack.execute(SetDeviceFields("d1", new_name="Core", mgmt_ip="10.0.0.1", mgmt_ip_set=True))
    assert t.devices["d1"].name == "Core"
    assert t.devices["d1"].mgmt_ip == "10.0.0.1"
    stack.undo()
    assert t.devices["d1"].name == "D1"
    assert t.devices["d1"].mgmt_ip is None


# [19] DUP_IP must catch duplicates within a single device.
def test_dup_ip_same_device():
    dev = Device("d1", "D1", "switch", mgmt_ip="10.0.0.5",
                 ports=[Port("p1", "p1", ip="10.0.0.5")])
    t = Topology(devices={"d1": dev})
    assert "DUP_IP" in {i.code for i in validate(t)}


# [18] save() must not leave a .tmp behind when serialization fails.
def test_save_cleans_tmp_on_failure(tmp_path):
    from core.project import NetwrightProject

    t = Topology()
    t.metadata = {"bad": {1, 2, 3}}  # a set is not JSON-serializable
    project = NetwrightProject(name="x", topology=t)
    path = tmp_path / "p.netwright"
    with pytest.raises(TypeError):
        project.save(path)
    assert not path.exists()
    assert not (tmp_path / "p.netwright.tmp").exists()


# [8] IOS export derives the netmask from the VLAN subnet (not hardcoded /24).
def test_ios_export_uses_subnet_mask():
    from core.export import export_device_cli

    t = Topology(vlans={10: Vlan(10, "Sales", subnet="10.0.10.0/25",
                                 gateway="10.0.10.1")})
    t.add_device(Device("sw1", "SW1", "switch", mgmt_ip="10.0.10.5",
                        ports=[Port("Gi0/1", "Gi0/1", mode="access", access_vlan=10)]))
    cfg = export_device_cli(t, "sw1")
    assert "255.255.255.128" in cfg  # /25, not /24
    assert "interface Vlan10" in cfg  # SVI emitted for the gateway


# [10] SVG node fill and legend swatch use the same color source.
def test_svg_color_consistency():
    from core.export import _fill_for_vlan, export_svg

    t = Topology(vlans={10: Vlan(10, "Sales", color="#123456")})
    t.add_device(Device("d1", "D1", "switch", x=10, y=10,
                        ports=[Port("p", "p", mode="access", access_vlan=10)]))
    svg = export_svg(t)
    assert _fill_for_vlan(t, 10) == "#123456"
    assert svg.count("#123456") >= 2  # node fill + legend swatch


# [13][15] validate_ops rejects string/missing VLAN fields (no crash downstream).
def test_validate_ops_rejects_bad_vlan_types():
    from ai.tools import validate_ops

    t = Topology()
    bad = [
        {"op": "add_acl", "src_vlan": "10", "dst_vlan": 20},  # string src
        {"op": "set_port_vlan", "device": "x"},                # missing port
        {"op": "add_link", "a_device": "x", "b_device": "y"},  # missing ports
    ]
    codes = {i.code for i in validate_ops(t, bad)}
    assert "OP_BAD_VLAN" in codes
    assert "OP_MISSING_FIELD" in codes


# [14] validate_ops tolerates a non-dict op without raising.
def test_validate_ops_non_dict_op():
    from ai.tools import validate_ops

    issues = validate_ops(Topology(), ["not-a-dict", {"op": "create_vlan", "id": 10}])
    assert any(i.code == "OP_MALFORMED" for i in issues)


# [5] edit_vlan / set_device ops now produce commands (not silently dropped).
def test_edit_vlan_and_set_device_ops_produce_commands():
    from ai.tools import ops_to_commands

    t = Topology(vlans={10: Vlan(10, "Sales")})
    t.add_device(Device("d1", "D1", "switch"))
    cmds = ops_to_commands(t, [
        {"op": "edit_vlan", "id": 10, "name": "Marketing"},
        {"op": "set_device", "device": "d1", "mgmt_ip": "10.0.0.1"},
    ])
    assert len(cmds) == 2
    stack = CommandStack(t)
    for c in cmds:
        stack.execute(c)
    assert t.vlans[10].name == "Marketing"
    assert t.devices["d1"].mgmt_ip == "10.0.0.1"


# ---- UI-level regressions (need an offscreen QApplication) ----------------
def test_apply_ops_blocks_mass_delete_without_force(qapp):
    from ui.dashboard import NetwrightWindow

    win = NetwrightWindow(auto_refresh=False)
    try:
        ids = [win.add_device_at("switch", i * 40.0, 0.0) for i in range(4)]
        ops = [{"op": "remove_device", "device": d} for d in ids]
        # [11] mass deletion is blocked unless explicitly forced
        assert win.apply_ops(ops) == 0
        assert len(win.project.topology.devices) == 4
        assert win.apply_ops(ops, force=True) == 4
        assert len(win.project.topology.devices) == 0
    finally:
        win.deleteLater()


def test_mgmt_ip_edit_is_undoable(qapp):
    from ui.dashboard import NetwrightWindow

    win = NetwrightWindow(auto_refresh=False)
    try:
        dev_id = win.add_device_at("switch", 0.0, 0.0)
        win.scene.device_items[dev_id].setSelected(True)
        win._load_device_page(dev_id)
        win.dev_mgmt_input.setText("10.0.0.9")
        n_before = win.undo_stack.count()
        win._apply_device_page()
        # [3][6] mgmt edit is one undoable command on the stack
        assert win.project.topology.devices[dev_id].mgmt_ip == "10.0.0.9"
        assert win.undo_stack.count() == n_before + 1
        win.undo_stack.undo()
        assert win.project.topology.devices[dev_id].mgmt_ip is None
    finally:
        win.deleteLater()


def test_apply_ops_allows_change_on_already_broken_design(qapp):
    from core.model import Device, Port, Topology
    from core.project import NetwrightProject
    from core.validate import validate
    from ui.dashboard import NetwrightWindow

    win = NetwrightWindow(auto_refresh=False)
    try:
        # Seed a pre-existing error: access port on an undefined VLAN 99.
        t = Topology()
        t.add_device(Device("s1", "S1", "switch",
                            ports=[Port("g0/1", "g0/1", mode="access", access_vlan=99)]))
        win.project = NetwrightProject(name="x", topology=t)
        win.refresh()
        assert any(i.code == "ACCESS_VLAN_UNDEFINED" for i in validate(t))
        # A benign, unrelated change still applies despite the pre-existing error.
        assert win.apply_ops([{"op": "create_vlan", "id": 10, "name": "Sales"}]) == 1
        # But a change that INTRODUCES a new error is blocked.
        assert win.apply_ops([{"op": "add_acl", "src_vlan": 10, "dst_vlan": 77}]) == 0
    finally:
        win.deleteLater()
