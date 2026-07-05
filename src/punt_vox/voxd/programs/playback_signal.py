"""Consume-path control signals: advance, play a named Part, cold-start from disk.

These map one-to-one onto the Program's consume-path transitions. ``Rotate`` is
posted by the loop on track-end and by a user skip/next alike (they are the same
Z transition); ``PlayPart`` and ``StartFromDisk`` carry the resolved target Part.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, final

if TYPE_CHECKING:
    from punt_vox.voxd.programs.part import Part
    from punt_vox.voxd.programs.program import Program

__all__ = ["PlayPart", "Rotate", "StartFromDisk"]


@final
@dataclass(frozen=True, slots=True)
class Rotate:
    """Advance to another ready Part (Z ``Rotate`` = skip = next = loop = end)."""

    @property
    def interrupts(self) -> bool:
        """A user skip acts now; the loop's own track-end advance sees no player."""
        return True

    def apply(self, program: Program) -> None:
        """Apply the advance transition."""
        program.rotate()


@final
@dataclass(frozen=True, slots=True)
class PlayPart:
    """Play a specific ready Part by name, without anti-repeat (Z ``PlayPart``)."""

    target: Part

    @property
    def interrupts(self) -> bool:
        """Playing a named Part starts it now."""
        return True

    def apply(self, program: Program) -> None:
        """Apply the explicit-play transition."""
        program.play_part(self.target)


@final
@dataclass(frozen=True, slots=True)
class StartFromDisk:
    """Cold-start playback from a saved pool with no fill (Z ``StartFromDisk``)."""

    target: Part

    @property
    def interrupts(self) -> bool:
        """Cold-start begins from ``off`` -- there is no playback to interrupt."""
        return False

    def apply(self, program: Program) -> None:
        """Apply the cold-start transition."""
        program.start_from_disk(self.target)
