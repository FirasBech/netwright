"""Thin QUndoCommand wrappers delegating to ``core.commands``.

The GUI pushes these onto a single QUndoStack; their ``redo``/``undo`` call into
the core Command so the model and the undo history share one mutation path.
"""
from __future__ import annotations

from PyQt5.QtWidgets import QUndoCommand

from core.commands import Command
from core.model import Topology


class CoreCommandWrapper(QUndoCommand):
    """Adapt a core Command to Qt's undo framework."""

    def __init__(self, topology: Topology, command: Command, text: str | None = None):
        super().__init__(text or getattr(command, "name", "Edit"))
        self._topology = topology
        self._command = command

    def redo(self) -> None:  # Qt calls redo() for the initial apply too
        self._command.do(self._topology)

    def undo(self) -> None:
        self._command.undo(self._topology)
