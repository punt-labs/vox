"""The ``PlayerDirectory`` seam -- resolve the *active* source's Part path, live.

The player never binds to one fixed directory: the active source switches at
runtime (a ``play`` swaps which pool is animated, and a union replay spans
directories), so the player asks the service to :meth:`locate` each Part it
spawns. This is the dynamic-player half of the vox-73m5 fix -- the path follows
the single writer's context swap, never a stale copy captured once at
construction -- generalised to resolve per-track for a union replay (finding #2).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pathlib import Path

    from punt_vox.voxd.programs.part import Part

__all__ = ["PlayerDirectory"]


class PlayerDirectory(Protocol):
    """Resolves a Part to its on-disk path for the active source (PY-DP-11)."""

    def locate(self, part: Part) -> Path:
        """Return the on-disk path of ``part`` for the active source, else raise.

        A generate Program returns its single directory joined with the Part's
        file; a union replay resolves each Part's opaque locator to a directory
        under the programs root.
        """
        ...
