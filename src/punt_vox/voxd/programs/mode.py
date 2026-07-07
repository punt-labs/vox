"""Program lifecycle modes and their coarse playback projection (Z ``Mode``)."""

from __future__ import annotations

from enum import StrEnum
from typing import Final

__all__ = ["Mode", "PlaybackStatus"]


class PlaybackStatus(StrEnum):
    """The coarse, client-facing playback state -- an observable collapse of ``Mode``.

    Distinct from ``Mode``: it merges ``generating_first`` into ``generating``
    and the two playing modes into ``playing``, so a status surface reports the
    state a client cares about without branching on the fine-grained mode.
    """

    OFF = "off"
    GENERATING = "generating"
    PLAYING = "playing"
    RETRYING = "retrying"
    FAILED = "failed"


class Mode(StrEnum):
    """The six-state Program machine (Z free type ``Mode``).

    The first four are the original playlist modes;
    ``RETRYING`` and ``FAILED`` are the resilience states of ``vox-ig52``. The
    modes are format-general -- a finite format reads ``PLAYING_ROTATING`` as
    "every Part generated, playing sequentially".
    """

    OFF = "off"
    GENERATING_FIRST = "generating_first"
    PLAYING_FILLING = "playing_filling"
    PLAYING_ROTATING = "playing_rotating"
    RETRYING = "retrying"
    FAILED = "failed"

    @property
    def status(self) -> PlaybackStatus:
        """Return the coarse playback status this mode presents to a client."""
        return _STATUS[self]


_STATUS: Final[dict[Mode, PlaybackStatus]] = {
    Mode.OFF: PlaybackStatus.OFF,
    Mode.GENERATING_FIRST: PlaybackStatus.GENERATING,
    Mode.PLAYING_FILLING: PlaybackStatus.PLAYING,
    Mode.PLAYING_ROTATING: PlaybackStatus.PLAYING,
    Mode.RETRYING: PlaybackStatus.RETRYING,
    Mode.FAILED: PlaybackStatus.FAILED,
}
