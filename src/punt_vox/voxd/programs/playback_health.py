"""The player's health -- an observability surface orthogonal to the model.

A player fault is deliberately **not** a Z Program transition: the Part stays
ready and the playback cursor stays put, so the domain state machine
(``docs/audio-programs.tex``) is untouched -- the player seam lives outside the
model. But a client must still see that audio is not reaching the speakers, so the
loop records the fault here and :class:`ProgramStatus` surfaces it. Reading a
daemon log is never a client interface.

Two distinct faults share this surface, tagged by :class:`PlaybackFaultKind`: a
*spawn* failure (the player binary could not be started -- a missing
``afplay``/``ffplay``, or an OS resource limit such as ``EMFILE``/``ENOMEM``) and a
*track exit* failure (the player started but exited non-zero on a missing or
corrupt track file). A client tells them apart from the ``kind`` field.

:class:`PlaybackHealth` is the single mutable slot the playback loop writes and the
status surface reads; :class:`PlaybackFault` is the immutable value it holds and the
value that crosses the wire.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Self, final

if TYPE_CHECKING:
    from punt_vox.voxd.programs.part import Part
    from punt_vox.voxd.programs.wire import JsonObject

__all__ = ["PlaybackFault", "PlaybackFaultKind", "PlaybackHealth"]


class PlaybackFaultKind(StrEnum):
    """Which class of player fault a :class:`PlaybackFault` records."""

    SPAWN = "spawn"  # the player binary could not be started
    TRACK_EXIT = "track_exit"  # the player ran but exited non-zero (bad track file)


@final
@dataclass(frozen=True, slots=True)
class PlaybackFault:
    """A player fault: which Part it happened for, its kind, and why."""

    part_index: int  # the intrinsic index of the Part the fault happened for
    reason: str  # the human-readable diagnostic (the OSError text or the exit code)
    kind: PlaybackFaultKind  # spawn failure vs non-zero track exit

    def to_dict(self) -> dict[str, object]:
        """Return the JSON object form -- the wire shape a client reads."""
        return {
            "part_index": self.part_index,
            "reason": self.reason,
            "kind": self.kind.value,
        }

    @classmethod
    def from_wire(cls, obj: JsonObject) -> Self:
        """Build a fault from a wire object, raising on a malformed record."""
        return cls(
            part_index=obj.require_int("part_index"),
            reason=obj.require_str("reason"),
            kind=PlaybackFaultKind(obj.require_str("kind")),
        )


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
