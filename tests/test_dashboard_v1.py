"""v1 dashboard features: undoable moves, per-op apply, trunk, theme, import."""
import json

from ai.assistant import propose_change
from ai.client import NetwrightAI

from _fakes import propose_response


def _window():
    from ui.dashboard import NetwrightWindow

    return NetwrightWindow(auto_refresh=False)


def test_record_move_is_undoable(qapp):
    win = _window()
    try:
        dev_id = win.add_device_at("switch", 0.0, 0.0)
        win.record_move(dev_id, 0.0, 0.0, 120.0, 80.0)
        dev = win.project.topology.devices[dev_id]
        assert (dev.x, dev.y) == (120.0, 80.0)
        win.undo_stack.undo()  # reverts the move
        assert (win.project.topology.devices[dev_id].x,
                win.project.topology.devices[dev_id].y) == (0.0, 0.0)
    finally:
        win.deleteLater()


def test_apply_ops_subset_only(qapp):
    win = _window()
    try:
        ops = [
            {"op": "create_vlan", "id": 40, "name": "Guest"},
            {"op": "create_vlan", "id": 50, "name": "Voice"},
        ]
        # apply only the first op
        n = win.apply_ops(ops[:1])
        assert n == 1
        assert 40 in win.project.topology.vlans
        assert 50 not in win.project.topology.vlans
    finally:
        win.deleteLater()


def test_apply_ops_rejects_invalid_subset(qapp):
    win = _window()
    try:
        # acl references a VLAN that isn't created in this subset -> invalid
        n = win.apply_ops([{"op": "add_acl", "src_vlan": 10, "dst_vlan": 20}])
        assert n == 0
        assert win.project.topology.acls == []
    finally:
        win.deleteLater()


def test_set_trunk(qapp):
    win = _window()
    try:
        dev_id = win.add_device_at("switch", 0.0, 0.0)
        win.create_vlan(10, "Sales")
        win.create_vlan(20, "Eng")
        win.set_trunk(dev_id, "Gi0/1", [10, 20], native=1)
        port = win.project.topology.devices[dev_id].ports[0]
        assert port.mode == "trunk"
        assert port.allowed_vlans == [10, 20]
        assert port.native_vlan == 1
    finally:
        win.deleteLater()


def test_theme_cycle(qapp):
    win = _window()
    try:
        start = win._theme
        win.cycle_theme()
        assert win._theme != start
        assert win._severity == __import__(
            "ui.theme", fromlist=["severity_colors"]
        ).severity_colors(win._theme)
    finally:
        win.deleteLater()


def test_import_policy(qapp, tmp_path):
    win = _window()
    try:
        path = tmp_path / "vlan_policy.json"
        path.write_text(json.dumps({"10": [20], "30": [30]}), encoding="utf-8")
        n = win.import_policy(str(path))
        assert n == 2  # permits: (10,20) and (30,30)
        pairs = {(a.src_vlan, a.dst_vlan) for a in win.project.topology.acls}
        assert (10, 20) in pairs and (30, 30) in pairs
        # importing again adds nothing (dedup)
        assert win.import_policy(str(path)) == 0
    finally:
        win.deleteLater()


def test_fit_to_view_runs(qapp):
    win = _window()
    try:
        win.add_device_at("switch", 0.0, 0.0)
        win.add_device_at("router", 300.0, 200.0)
        win.canvas.fit_to_view()  # should not raise
    finally:
        win.deleteLater()
