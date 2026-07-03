"""One-click Windows launcher for the Netwright dashboard (no console window).

Claims the AppUserModelID *before* importing PyQt so the taskbar groups the
window under the Netwright icon, mirroring SecureLink's launcher.
"""
from __future__ import annotations

import os
import sys

# Claim a stable taskbar identity before any Qt import (Windows only).
try:  # pragma: no cover - platform specific
    import ctypes

    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
        "FirasBech.Netwright.Dashboard"
    )
except Exception:  # pragma: no cover - non-Windows or restricted env
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ui.dashboard import launch_dashboard  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(launch_dashboard())
