"""The player seam -- spawn a process for a Part and control its lifetime.

``ProgramLoop`` owns *when* to play, advance, and stop; ``Player`` owns *how* a
Part becomes a running process. Production injects a subprocess player; tests
inject a fake whose process end they control, so the loop's advance/interrupt
logic is exercised without a real subprocess.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from punt_vox.voxd.programs.part import Part

__all__ = ["Player", "PlayerProcess"]


class PlayerProcess(Protocol):
    """A running player whose end the loop waits for and can cut short."""

    async def wait(self) -> int:
        """Block until the player exits and return its exit code."""
        ...

    async def kill(self) -> None:
        """Stop the player now (a skip / off / play-a-part interrupt)."""
        ...


class Player(Protocol):
    """Turn a ready Part into a running :class:`PlayerProcess` (PY-DP-11)."""

    async def play(self, part: Part) -> PlayerProcess:
        """Start playing ``part`` and return its process handle."""
        ...
