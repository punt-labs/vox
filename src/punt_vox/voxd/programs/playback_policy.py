"""The playback-advance strategy seam (Z ``Rotate`` next-Part choice).

The loop-versus-stop decision lives here, not in a new ``Mode``: a playlist
policy never ends, while a finite format's policy signals end-of-list. Returning
a discriminated ``AdvanceResult`` -- an ``Advance`` or the ``Complete`` singleton
-- rather than a bare ``Part | None`` lets a finite-format policy be added
with no change to this Protocol.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Protocol, final

from punt_vox.voxd.programs.part import Part

__all__ = ["COMPLETE", "Advance", "AdvanceResult", "Complete", "PlaybackPolicy"]


@dataclass(frozen=True, slots=True)
class Advance:
    """The next Part to play (Z ``Rotate`` with a chosen successor)."""

    part: Part


@final
class Complete:
    """End-of-list signal: a finite format has no further Part to play.

    A playlist never reaches this -- a playlist has no end -- but the
    shared result type carries it so a sequential policy can signal
    end-of-list without changing the Protocol. Use the ``COMPLETE`` singleton.
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return "COMPLETE"


COMPLETE: Final = Complete()

type AdvanceResult = Advance | Complete


class PlaybackPolicy(Protocol):
    """Choose the next Part to play from the current pool (single-method, PY-DP-11)."""

    def next_part(self, pool: tuple[Part, ...], playing: Part | None) -> AdvanceResult:
        """Return the next Part (``Advance``) or signal end-of-list (``COMPLETE``).

        ``pool`` is the ready Parts ordered by intrinsic index; ``playing`` is
        the Part currently playing, or ``None``. A playlist policy avoids an
        immediate repeat when the pool holds more than one Part and replays the
        sole Part otherwise, and never returns ``COMPLETE``.
        """
        ...
