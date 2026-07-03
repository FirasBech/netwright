"""Shared test setup: import path, offscreen Qt, no API key, deterministic ids."""
from __future__ import annotations

import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# UI tests run headless; a stray real API call must fail loudly, not bill.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.pop("ANTHROPIC_API_KEY", None)


@pytest.fixture(autouse=True)
def _reset_ids():
    from core.ids import reset_ids

    reset_ids()
    yield


@pytest.fixture(scope="session")
def qapp():
    """A single offscreen QApplication for the UI tests (PyQt5 imported lazily)."""
    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def sample_topology():
    """A tiny two-switch topology with VLANs, used across tests."""
    from core.model import Device, Link, Port, Topology, Vlan

    t = Topology(name="Test")
    t.vlans = {
        10: Vlan(10, "Sales", subnet="10.0.10.0/24", gateway="10.0.10.1"),
        20: Vlan(20, "Eng", subnet="10.0.20.0/24", gateway="10.0.20.1"),
    }
    sw1 = Device("sw1", "SW1", "switch", x=0, y=0, ports=[Port("g0/1", "g0/1")])
    sw2 = Device("sw2", "SW2", "switch", x=200, y=0, ports=[Port("g0/1", "g0/1")])
    sw1.ports[0].access_vlan = 10
    sw2.ports[0].access_vlan = 20
    t.add_device(sw1)
    t.add_device(sw2)
    t.add_link(Link("ln1", "sw1", "g0/1", "sw2", "g0/1", kind="ethernet"))
    return t
