"""Consume-path control signals: advance, play a named Part, cold-start from disk.

``Rotate`` is source-agnostic (Z ``Rotate`` / ``RadioRotate``): it advances
whichever source is active, so it drives a generate Program and a replay Selection
alike. ``PlayPart`` and ``StartFromDisk`` are generate-only: they narrow
``isinstance(source, Program)`` and reject as a lost race against a Selection.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, final

from punt_vox.voxd.programs.guard import GuardViolationError
from punt_vox.voxd.programs.program import Program

if TYPE_CHECKING:
    from punt_vox.voxd.programs.part import Part
    from punt_vox.voxd.programs.playback_source import PlaybackSource

__all__ = ["PlayPart", "Rotate", "StartFromDisk"]


@final
@dataclass(frozen=True, slots=True)
class Rotate:
    """Advance to another Part (Z ``Rotate`` = ``RadioRotate`` = skip = next = end)."""

    @property
    def interrupts(self) -> bool:
        """A user skip acts now; the loop's own track-end advance sees no player."""
        return True

    def apply(self, source: PlaybackSource, /) -> None:
        """Advance whichever source is active (generate Program or replay Selection)."""
        source.rotate()


@final
@dataclass(frozen=True, slots=True)
class PlayPart:
    """Play a specific ready Part by name, without anti-repeat (Z ``PlayPart``)."""

    target: Part

    @property
    def interrupts(self) -> bool:
        """Playing a named Part starts it now."""
        return True

    def apply(self, source: PlaybackSource, /) -> None:
        """Play a named Part on a generate Program, rejecting a replay Selection."""
        if not isinstance(source, Program):
            GuardViolationError.reject("play_part requires a generate program")
        source.play_part(self.target)


@final
@dataclass(frozen=True, slots=True)
class StartFromDisk:
    """Cold-start playback from a saved pool with no fill (Z ``StartFromDisk``)."""

    target: Part

    @property
    def interrupts(self) -> bool:
        """Cold-start begins from ``off`` -- there is no playback to interrupt."""
        return False

    def apply(self, source: PlaybackSource, /) -> None:
        """Cold-start a generate Program, rejecting a replay Selection."""
        if not isinstance(source, Program):
            GuardViolationError.reject("start_from_disk requires a generate program")
        source.start_from_disk(self.target)
