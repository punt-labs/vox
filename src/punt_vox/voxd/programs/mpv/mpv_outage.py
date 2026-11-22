"""``MpvOutage`` -- count one mpv outage, cap its restarts, and log it once.

An outage starts at the first failed bring-up or crash and ends when mpv passes
its IPC handshake again (``recovered``) or the restart cap is spent
(``failed``). The cap counts only *consecutive* attempts that never reached
``ready`` (I4), so a crash that recovers does not accumulate toward it.

A host without mpv, or an mpv that keeps dying, walks the whole cap. Logging
every attempt at ERROR buries the daemon log in noise for a condition the
operator learns from once, so each outage is told as an onset WARNING, DEBUG
detail per further attempt, and a single outcome. Only the handshake, never the
spawn, earns the ``mpv ready`` record.
"""

from __future__ import annotations

import logging
from typing import Self, final

from punt_vox.types_programs.playback_fault import PlaybackFaultKind

__all__ = ["MpvOutage"]

logger = logging.getLogger(__name__)


@final
class MpvOutage:
    """Track consecutive mpv failures against the restart cap and log the outage."""

    __slots__ = ("_ever_ready", "_failures", "_max_restarts")
    _ever_ready: bool
    _failures: int
    _max_restarts: int

    def __new__(cls, max_restarts: int) -> Self:
        """Create an outage tracker allowing at most *max_restarts* restarts."""
        self = super().__new__(cls)
        self._ever_ready = False
        self._failures = 0
        self._max_restarts = max_restarts
        return self

    def bring_up_failed(self) -> None:
        """Count a spawn/connect failure: WARNING at onset, DEBUG thereafter."""
        self._failures += 1
        if self._failures == 1:
            logger.warning(
                "mpv unavailable; music playback degraded, retrying up to %d times",
                self._max_restarts,
            )
            return
        logger.debug("mpv bring-up failed: failures=%d", self._failures)

    def crashed(self) -> None:
        """Count a crash of a running mpv, the onset of an outage."""
        self._failures += 1
        logger.warning(
            "mpv crashed; music playback degraded, restarting up to %d times",
            self._max_restarts,
        )

    def ready(self) -> None:
        """Close any open outage: mpv passed its IPC handshake."""
        if self._failures:
            logger.info("mpv outcome=recovered failures=%d", self._failures)
        else:
            logger.info("mpv ready")
        self._ever_ready = True
        self._failures = 0

    def may_restart(self) -> bool:
        """Return whether the outage is still within the restart cap (I4)."""
        return self._failures <= self._max_restarts

    def gave_up(self) -> PlaybackFaultKind:
        """Log the ``failed`` outcome and return the standing fault's kind.

        An mpv that never ran is ``PLAYER_UNAVAILABLE`` -- a missing or broken
        install, logged at WARNING; one that ran and then died for good is
        ``PLAYER_FAILED``, logged at ERROR.
        """
        if self._ever_ready:
            kind, level = PlaybackFaultKind.PLAYER_FAILED, logging.ERROR
        else:
            kind, level = PlaybackFaultKind.PLAYER_UNAVAILABLE, logging.WARNING
        logger.log(
            level,
            "mpv outcome=failed failures=%d; music playback unavailable",
            self._failures,
        )
        return kind
