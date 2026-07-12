"""The player's live health slot -- written by the loop, read by status.

The immutable :class:`PlaybackFault` value and its ``PlaybackFaultKind`` tag are
the wire shape and live in :mod:`punt_vox.types_programs.playback_fault`; this
module holds only the single mutable slot the playback loop writes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, final

from punt_vox.types_programs.playback_fault import PlaybackFault, PlaybackFaultKind

if TYPE_CHECKING:
    from punt_vox.voxd.programs.part import Part

__all__ = ["PlaybackFault", "PlaybackFaultKind", "PlaybackHealth"]


@final
class PlaybackHealth:
    """The player's live health -- written by the loop, read by status.

    ``None`` fault means the player is healthy (the last spawn succeeded, or none
    has been attempted). A recorded fault persists until the next successful spawn
    clears it, so a client polling ``status`` sees a *standing* problem, not a
    single edge that scrolled past in a log.
    """

    __slots__ = ("_fault",)
    _fault: PlaybackFault | None  # None means healthy -- the documented contract

    def __new__(cls) -> Self:
        self = super().__new__(cls)
        self._fault = None
        return self

    def record(self, part: Part, reason: str, kind: PlaybackFaultKind) -> None:
        """Record a ``kind`` fault for ``part`` so status can surface it."""
        self._fault = PlaybackFault(part_index=part.index, reason=reason, kind=kind)

    def clear(self) -> None:
        """Clear the fault after a successful spawn (the player recovered)."""
        self._fault = None

    @property
    def fault(self) -> PlaybackFault | None:
        """Return the standing fault, or ``None`` when the player is healthy."""
        return self._fault
