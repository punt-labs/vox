"""The ``PlaybackSource`` protocol -- the source union the loop and channel animate.

The daemon plays exactly one source at a time, and that source is a union: a
generate-mode :class:`Program` or a consume-only :class:`SelectionPlayback`. Both
satisfy this narrowed structural interface, so the single-writer channel and the
playback loop drive either without narrowing on the concrete type (risk R1). The
protocol carries no ``filling`` and no ``locate`` (findings #1, #2): path
resolution is the persistence seam's job, and "wants generation" is the one
generation signal the fill reconciler reads.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from punt_vox.voxd.programs.part import Part

__all__ = ["PlaybackSource"]


@runtime_checkable
class PlaybackSource(Protocol):
    """The source the loop plays and the channel retargets (Program or Selection)."""

    @property
    def playing(self) -> Part | None:
        """Return the Part currently playing, or ``None``."""
        ...

    def rotate(self) -> None:
        """Advance the cursor to another Part (auto-advance, next, or skip)."""
        ...

    @property
    def wants_generation(self) -> bool:
        """Whether the fill reconciler should keep generation running.

        ``True`` for a :class:`Program` that is filling or retrying (preserving
        the vox-ig52 stranded-retry clause); ``False`` for a Selection, which
        never generates (finding #1).
        """
        ...

    @property
    def is_playing(self) -> bool:
        """Whether the loop should auto-advance this source on a track end.

        The source-agnostic advance gate (F#6): a :class:`Program` reports its
        mode gate, a Selection reports ``playing is not None`` -- so a radio
        auto-advances on track-end exactly as a generate pool does.
        """
        ...
