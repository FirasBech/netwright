"""Deleting selected devices/links from the canvas (Del key / toolbar / menu)."""


def _window():
    from ui.dashboard import NetwrightWindow

    return NetwrightWindow(auto_refresh=False)


def test_delete_selected_device_is_undoable(qapp):
    win = _window()
    try:
        a = win.add_device_at("router", 0.0, 0.0)
        b = win.add_device_at("switch", 200.0, 0.0)
        win.create_link(a, b)
        assert a in win.project.topology.devices

        # select the device on the canvas and delete it
        win.scene.device_items[a].setSelected(True)
        n = win.delete_selected()
        assert n == 1
        assert a not in win.project.topology.devices
        assert not win.project.topology.links  # incident link went too

        win.undo_stack.undo()  # one step restores device AND its link
        assert a in win.project.topology.devices
        assert len(win.project.topology.links) == 1
    finally:
        win.deleteLater()


def test_delete_with_nothing_selected_is_noop(qapp):
    win = _window()
    try:
        win.add_device_at("switch", 0.0, 0.0)
        assert win.delete_selected() == 0
    finally:
        win.deleteLater()


def test_delete_action_has_del_shortcut(qapp):
    from PyQt5.QtGui import QKeySequence

    win = _window()
    try:
        seqs = win.delete_action.shortcuts()
        assert QKeySequence(0x01000007) in seqs or any(  # Qt.Key_Delete
            s == QKeySequence.Delete for s in seqs
        )
    finally:
        win.deleteLater()


def test_credential_dialog_builds_ssh_creds(qapp):
    from ui.dashboard import CredentialDialog

    dlg = CredentialDialog(None, "ssh")
    dlg.host.setText("10.0.0.1")
    dlg.username.setText("admin")
    dlg.password.setText("secret")
    dlg.device_type.setCurrentText("huawei")
    creds = dlg.credentials()
    assert len(creds) == 1
    assert creds[0].host == "10.0.0.1"
    assert creds[0].device_type == "huawei"
    assert creds[0].neighbor_command() == "display lldp neighbor"
    dlg.deleteLater()


def test_credential_dialog_builds_snmp_creds(qapp):
    from ui.dashboard import CredentialDialog

    dlg = CredentialDialog(None, "snmp")
    dlg.host.setText("10.0.0.2")
    dlg.community.setText("public")
    creds = dlg.credentials()
    assert creds[0].host == "10.0.0.2"
    assert creds[0].community == "public"
    dlg.deleteLater()
