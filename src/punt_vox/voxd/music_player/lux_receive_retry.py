"""Receive-leg outage accounting, quiet retries, and terminal outcomes."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Self, final

from punt_lux import HubUnavailableError

from punt_vox.lux_common import HubOutageLog
from punt_vox.voxd.music_player.lux_fault_boundary import LuxFaultBoundary
from punt_vox.voxd.music_player.lux_trace import LuxTrace

logger = logging.getLogger(__name__)
_trace = LuxTrace(logger)


@final
class LuxReceiveRetry:
    """Own one outage count from first failure to recovery or termination."""

    __slots__ = ("_failures", "_outage")
    _failures: int
    _outage: HubOutageLog

    def __new__(cls, outage: HubOutageLog) -> Self:
        """Bind the shared Lux outage window."""
        self = super().__new__(cls)
        self._failures = 0
        self._outage = outage
        return self

    async def run(self, listen: Callable[[], Awaitable[None]], delay: float) -> None:
        """Retry until a clean stop, preserving cancellation and summarizing outages."""
        _trace.info("music receive leg connecting")
        try:
            while not await self._listen_once(listen):
                await asyncio.sleep(delay)
            _trace.info("music receive leg stopped cleanly")
        finally:
            if self._failures:
                _trace.info(
                    "music receive leg outcome=terminated failures=%d", self._failures
                )
                self._failures = 0

    async def _listen_once(self, listen: Callable[[], Awaitable[None]]) -> bool:
        """Attempt one cycle, logging only fixed failure classifications."""
        with LuxFaultBoundary(logger, "music receive leg failed", logging.DEBUG):
            try:
                _trace.debug("music receive leg connecting")
                await listen()
                return True
            except HubUnavailableError:
                self._note_failure()
                return False
        self._note_failure()
        return False

    def _note_failure(self) -> None:
        """Open the shared outage once; keep all repeat failures quiet."""
        if not self._failures:
            self._outage.note("[lux] luxd down or music receive leg failed; retrying")
        else:
            _trace.debug("music receive leg failed; retrying")
        self._failures += 1

    def connected(self) -> None:
        """Close an outage exactly once at the successful handshake."""
        if self._failures:
            _trace.info(
                "music receive leg outcome=recovered failures=%d", self._failures
            )
            self._failures = 0
        else:
            _trace.info(
                "hub handshake complete; re-registering menu and installing scene"
            )
        self._outage.clear()
