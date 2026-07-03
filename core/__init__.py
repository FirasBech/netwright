"""Netwright core — pure domain logic.

This package contains the topology data model, validation engine, undo/redo
command funnel, project serialization, the vendored VLAN policy engine, and
export helpers. It depends on nothing in ``ui`` or ``ai`` and must never import
PyQt5 (enforced by ``tests/test_no_pyqt_in_core.py``).
"""
