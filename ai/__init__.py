"""Netwright AI — Anthropic Claude integration.

Imports ``core`` only, never ``ui``, and never PyQt5. The assistant edits the
topology only by emitting validated ops that the app turns into Commands; the
model proposes, the deterministic validator disposes, and the user approves a
diff before anything is applied.
"""
