"""The player-fault wire value types -- an observability surface, not a state.

A player fault is deliberately **not** a Z Program transition: the Part stays
ready and the playback cursor stays put, so the domain state machine is
untouched. But a client must still see that audio is not reaching the speakers,
so the loop records the fault and :class:`~punt_vox.types_programs.status.ProgramStatus`
surfaces it. Reading a daemon log is never a client interface.

Two distinct faults share this surface, tagged by :class:`PlaybackFaultKind`: a
*spawn* failure (the player binary could not be started) and a *track exit*
failure (the player started but exited non-zero on a missing or corrupt file).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Self, final

if TYPE_CHECKING:
    from punt_vox.types_programs.wire import JsonObject

__all__ = ["PlaybackFault", "PlaybackFaultKind"]


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
