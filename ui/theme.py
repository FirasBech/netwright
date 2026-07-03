"""Theme palettes — "simplified professional", one accent, generous whitespace.

Three variants: ``dark`` (default), ``light``, and ``high_contrast``. ``PALETTE``
and ``APP_QSS`` remain the dark defaults for back-compat; ``build_qss(name)`` and
``palette_for(name)`` return any variant.
"""
from __future__ import annotations

PALETTES = {
    "dark": {
        "bg": "#0f172a", "panel": "#1e293b", "border": "#334155",
        "text": "#e2e8f0", "muted": "#94a3b8",
        "accent": "#2563eb", "accent_hover": "#3b82f6", "accent_pressed": "#1d4ed8",
        "error": "#f87171", "warning": "#fbbf24", "info": "#38bdf8",
        "grid_minor": "#1e293b", "grid_major": "#334155",
    },
    "light": {
        "bg": "#f8fafc", "panel": "#ffffff", "border": "#cbd5e1",
        "text": "#0f172a", "muted": "#64748b",
        "accent": "#2563eb", "accent_hover": "#3b82f6", "accent_pressed": "#1d4ed8",
        "error": "#dc2626", "warning": "#d97706", "info": "#0284c7",
        "grid_minor": "#eef2f7", "grid_major": "#dbe3ec",
    },
    "high_contrast": {
        "bg": "#000000", "panel": "#0a0a0a", "border": "#ffffff",
        "text": "#ffffff", "muted": "#d4d4d4",
        "accent": "#ffd400", "accent_hover": "#ffe34d", "accent_pressed": "#e6c000",
        "error": "#ff5555", "warning": "#ffd400", "info": "#55ddff",
        "grid_minor": "#1a1a1a", "grid_major": "#333333",
    },
}


def palette_for(name: str) -> dict:
    return PALETTES.get(name, PALETTES["dark"])


def build_qss(name: str = "dark") -> str:
    p = palette_for(name)
    return f"""
QMainWindow, QWidget {{
    background-color: {p['bg']};
    color: {p['text']};
    font-family: "Segoe UI", system-ui, sans-serif;
    font-size: 13px;
}}
QDockWidget, QDockWidget::title {{
    background-color: {p['panel']};
    color: {p['text']};
    titlebar-close-icon: none;
}}
QTabWidget::pane {{ border: 1px solid {p['border']}; }}
QTabBar::tab {{
    background: {p['panel']};
    color: {p['muted']};
    padding: 6px 14px;
    border: 1px solid {p['border']};
}}
QTabBar::tab:selected {{ color: {p['text']}; background: {p['bg']}; }}
QGroupBox {{ border: 1px solid {p['border']}; border-radius: 6px; margin-top: 8px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 4px; }}
QPushButton {{
    background-color: {p['accent']};
    color: white;
    border: none;
    border-radius: 6px;
    padding: 7px 14px;
}}
QPushButton:hover {{ background-color: {p['accent_hover']}; }}
QPushButton:pressed {{ background-color: {p['accent_pressed']}; }}
QPushButton:disabled {{ background-color: {p['border']}; color: {p['muted']}; }}
QListWidget, QTreeWidget, QTextBrowser, QPlainTextEdit, QLineEdit, QSpinBox {{
    background-color: {p['panel']};
    border: 1px solid {p['border']};
    border-radius: 6px;
}}
QToolBar {{ background: {p['panel']}; border-bottom: 1px solid {p['border']}; spacing: 4px; }}
QStatusBar {{ background: {p['panel']}; color: {p['muted']}; }}
"""


def severity_colors(name: str = "dark") -> dict:
    p = palette_for(name)
    return {"error": p["error"], "warning": p["warning"], "info": p["info"]}


# Back-compat defaults (dark).
PALETTE = PALETTES["dark"]
APP_QSS = build_qss("dark")
SEVERITY_COLOR = severity_colors("dark")
