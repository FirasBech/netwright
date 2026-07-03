"""Short, human-readable, deterministic-for-tests identifiers.

``new_id('sw') -> 'sw-3f9a'``. A module-level counter keeps ids stable and
collision-free without ``uuid``/``random`` (which would make the model
non-deterministic and break golden tests). Call :func:`reset_ids` in test
setup for reproducible output.
"""
from __future__ import annotations

import itertools

_counter = itertools.count(1)


def new_id(prefix: str = "id") -> str:
    """Return a new id like ``'<prefix>-000a'`` (4 hex digits, monotonic)."""
    n = next(_counter)
    return f"{prefix}-{n:04x}"


def reset_ids(start: int = 1) -> None:
    """Reset the id counter (test helper)."""
    global _counter
    _counter = itertools.count(start)
