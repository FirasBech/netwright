"""Finishing features: autosave/.bak/recovery, theme persistence, minimap, glyphs."""
import os


def _window():
    from ui.dashboard import NetwrightWindow

    return NetwrightWindow(auto_refresh=False)


def test_autosave_only_when_dirty(qapp, tmp_path):
    win = _window()
    try:
        win._path = str(tmp_path / "p.netwright")
        assert win.autosave_now() is None  # clean -> no autosave
        win.add_device_at("switch", 0.0, 0.0)  # dirty
        auto = win.autosave_now()
        assert auto == win._path + ".autosave"
        assert os.path.exists(auto)
    finally:
        win.deleteLater()


def test_save_to_rotates_bak_and_clears_autosave(qapp, tmp_path):
    win = _window()
    try:
        path = str(tmp_path / "p.netwright")
        win.add_device_at("switch", 0.0, 0.0)
        win._path = path
        win.autosave_now()
        win.save_to(path)  # first save: no .bak yet, autosave removed
        assert os.path.exists(path)
        assert not os.path.exists(path + ".autosave")
        assert not os.path.exists(path + ".bak")
        assert win.undo_stack.isClean()
        win.add_device_at("router", 100.0, 0.0)
        win.save_to(path)  # second save rotates the previous file to .bak
        assert os.path.exists(path + ".bak")
    finally:
        win.deleteLater()


def test_recovery_loads_newer_autosave(qapp, tmp_path):
    from ui.dashboard import NetwrightWindow

    win = _window()
    try:
        path = str(tmp_path / "p.netwright")
        # Save a one-device project, then leave a newer two-device autosave.
        d1 = win.add_device_at("switch", 0.0, 0.0)
        win.save_to(path)
        win.add_device_at("router", 100.0, 0.0)
        win.project.save(path + ".autosave")
        os.utime(path + ".autosave", None)  # ensure newer mtime
        assert NetwrightWindow.find_autosave(path) == path + ".autosave"

        fresh = _window()
        try:
            fresh.load_path(path, recover=True)
            assert len(fresh.project.topology.devices) == 2
            assert fresh._recovered is True  # recovered content counts as unsaved
            fresh.load_path(path, recover=False)
            assert len(fresh.project.topology.devices) == 1
        finally:
            fresh.deleteLater()
    finally:
        win.deleteLater()


def test_theme_persists_to_settings(qapp, monkeypatch):
    win = _window()
    try:
        saved = {}
        monkeypatch.setattr(
            type(win.settings), "save",
            lambda self, base_dir=None: saved.update(self.as_dict()),
        )
        win.apply_theme("light")
        assert saved.get("theme") == "light"
    finally:
        win.deleteLater()


def test_minimap_exists_and_updates(qapp):
    win = _window()
    try:
        assert win.minimap.scene() is win.scene  # shared scene, second view
        win.add_device_at("switch", 0.0, 0.0)
        win._update_minimap()  # must not raise
    finally:
        win.deleteLater()


def test_glyph_renderer_loads_switch(qapp):
    from ui.canvas import glyph_renderer

    renderer = glyph_renderer("switch")
    # QtSvg ships with PyQt5 wheels; the asset exists in-repo.
    assert renderer is not None and renderer.isValid()
    assert glyph_renderer("not-a-kind") is None
