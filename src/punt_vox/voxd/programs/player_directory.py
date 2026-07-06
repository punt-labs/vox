"""The ``PlayerDirectory`` seam -- yield the *active* Program's directory, live.

The player never binds to one fixed directory: the active Program switches at
runtime (a ``play <name>`` swaps which pool is animated), so the player asks the
service for the live directory each time it spawns a Part. This is the
dynamic-player half of the vox-73m5 fix -- the directory follows the single
writer's context swap, never a stale copy captured once at construction.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["PlayerDirectory"]


class PlayerDirectory(Protocol):
    """Yields the directory backing the currently active Program (PY-DP-11)."""

    def active_directory(self) -> Path:
        """Return the active Program's directory, raising when the daemon is idle."""
        ...
