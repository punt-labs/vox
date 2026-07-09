"""How a playing track stopped -- the value the interrupt race hands the loop.

A track can stop three ways, and the playback loop reacts differently to each: a
user interrupt (skip / off / play-a-part) or a *raised* player ``wait`` means the
loop kills and does not advance; a clean exit (code 0) means advance; a non-zero
exit means the track *faulted* -- the loop records it on ``PlaybackHealth`` so a
client sees the fault via status, then advances past the bad track. Folding
these into one immutable value keeps :class:`InterruptRace` returning *what
happened* and leaves *what to do about it* to the loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import final

__all__ = ["TrackEnd"]


@final
@dataclass(frozen=True, slots=True)
class TrackEnd:
    """The outcome of one played track: interrupted, cleanly ended, or faulted."""

    # True for a user interrupt or a raised player wait -- the loop kills and does
    # not advance; exit_code is then None (no settled code to report).
    interrupted: bool
    # The player's exit code when it settled on its own; None when interrupted or
    # when wait raised. 0 is a clean end; non-zero is a fault.
    exit_code: int | None

    @property
    def faulted(self) -> bool:
        """Whether a non-interrupted track failed (a non-zero player exit, F3)."""
        return not self.interrupted and self.exit_code not in (None, 0)
