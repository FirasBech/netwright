"""Enforce the one-way dependency boundary: core/ and ai/ never import PyQt5."""
import importlib
import sys


CORE_MODULES = [
    "core.model",
    "core.ids",
    "core.policy",
    "core.settings",
    "core.project",
    "core.commands",
    "core.validate",
    "core.export",
]
AI_MODULES = ["ai.client", "ai.tools", "ai.prompts", "ai.assistant"]


def test_core_and_ai_do_not_import_pyqt5():
    # Drop any PyQt5 that another test imported first.
    for name in list(sys.modules):
        if name.startswith("PyQt5"):
            del sys.modules[name]

    for mod in CORE_MODULES + AI_MODULES:
        importlib.import_module(mod)

    leaked = [n for n in sys.modules if n.startswith("PyQt5")]
    assert leaked == [], f"core/ai pulled in PyQt5: {leaked}"
