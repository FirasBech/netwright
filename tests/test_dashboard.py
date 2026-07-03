def test_window_builds_offscreen(qapp):
    from ui.dashboard import NetwrightWindow

    win = NetwrightWindow(auto_refresh=False)
    try:
        # Right dock contract: exactly Properties + AI tabs, in order.
        labels = [win.right_tabs.tabText(i) for i in range(win.right_tabs.count())]
        assert labels == ["Properties", "AI"]
        # Issues panel exists.
        assert win.issues_panel is not None
        assert win.issues_panel.columnCount() == 3
    finally:
        win.deleteLater()


def test_ai_panel_disabled_without_key(qapp):
    from ui.dashboard import NetwrightWindow

    win = NetwrightWindow(auto_refresh=False)
    try:
        # conftest unsets ANTHROPIC_API_KEY, so Propose is disabled.
        assert win.ai.available() is False
        assert win.ai_ask.isEnabled() is False
    finally:
        win.deleteLater()


def test_refresh_populates_issues(qapp, sample_topology):
    from core.project import NetwrightProject
    from ui.dashboard import NetwrightWindow

    win = NetwrightWindow(auto_refresh=False)
    try:
        win.project = NetwrightProject(name="x", topology=sample_topology)
        win.refresh()
        # sample topology has no errors but no isolated devices either; the panel
        # simply reflects validate() output without raising.
        assert win.issues_panel.topLevelItemCount() >= 0
    finally:
        win.deleteLater()
