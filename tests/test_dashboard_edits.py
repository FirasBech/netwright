"""Controller-level tests for the dashboard's single undo path and AI apply."""
from ai.assistant import propose_change
from ai.client import NetwrightAI

from _fakes import propose_response


def _window():
    from ui.dashboard import NetwrightWindow

    return NetwrightWindow(auto_refresh=False)


def test_add_device_is_undoable(qapp):
    win = _window()
    try:
        dev_id = win.add_device_at("switch", 40.0, 40.0)
        assert dev_id in win.project.topology.devices
        win.undo_stack.undo()
        assert dev_id not in win.project.topology.devices
        win.undo_stack.redo()
        assert dev_id in win.project.topology.devices
    finally:
        win.deleteLater()


def test_create_and_delete_vlan(qapp):
    win = _window()
    try:
        win.create_vlan(30, "Guest")
        assert 30 in win.project.topology.vlans
        win.delete_vlan(30)
        assert 30 not in win.project.topology.vlans
        win.undo_stack.undo()  # undo the delete
        assert 30 in win.project.topology.vlans
    finally:
        win.deleteLater()


def test_create_link_picks_distinct_free_ports(qapp):
    from core.validate import validate

    win = _window()
    try:
        a = win.add_device_at("switch", 0.0, 0.0)
        b = win.add_device_at("switch", 200.0, 0.0)
        link_id = win.create_link(a, b)
        assert link_id in win.project.topology.links
        codes = {i.code for i in validate(win.project.topology)}
        assert "PORT_DOUBLE_LINKED" not in codes
        assert "DANGLING_LINK" not in codes
    finally:
        win.deleteLater()


def test_apply_ai_proposal_is_one_undo_step(qapp):
    win = _window()
    try:
        ops = [
            {"op": "create_vlan", "id": 40, "name": "Guest"},
            {"op": "add_acl", "src_vlan": 40, "dst_vlan": 40, "action": "permit"},
        ]
        ai = NetwrightAI(client=propose_response("Add guest + permit", ops))
        change = propose_change(win.project.topology, "guest vlan", ai)
        n = win.apply_proposal(change)
        assert n == 2
        assert 40 in win.project.topology.vlans
        assert len(win.project.topology.acls) == 1
        # one undo reverts the whole batch
        win.undo_stack.undo()
        assert 40 not in win.project.topology.vlans
        assert win.project.topology.acls == []
    finally:
        win.deleteLater()


def test_rejected_proposal_is_not_applied(qapp):
    win = _window()
    try:
        ops = [{"op": "create_vlan", "id": 9999, "name": "Bad"}]  # out of range
        ai = NetwrightAI(client=propose_response("Bad", ops))
        change = propose_change(win.project.topology, "bad", ai)
        assert win.apply_proposal(change) == 0  # applicable is False
        assert 9999 not in win.project.topology.vlans
    finally:
        win.deleteLater()


def test_overlay_toggle_changes_node_color(qapp):
    from ui.canvas import vlan_color

    win = _window()
    try:
        dev_id = win.add_device_at("switch", 0.0, 0.0)
        win.create_vlan(10, "Sales")
        win.set_port_access_vlan(dev_id, "Gi0/1", 10)
        win.toggle_overlay(True)
        assert win.scene.device_items[dev_id]._color == vlan_color(10)
        win.toggle_overlay(False)
        assert win.scene.device_items[dev_id]._color != vlan_color(10)
    finally:
        win.deleteLater()
