"""The background reader of one ``pi --mode rpc`` process's stderr.

pi's stderr is diagnostics only -- it is never spoken -- but it must be read
continuously: a child blocked on a full stderr pipe stops writing stdout, the
classic deadlock. :class:`PiStderr` drains it for the life of the process and
keeps the last few lines, which are usually why pi exited, so the error that
ends a call can name the cause.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import Self, final

__all__ = ["PiStderr"]

logger = logging.getLogger(__name__)

_TAIL_LINES = 20

# How long to wait for stderr's last lines once pi has stopped.
_SETTLE_S = 1.0


@final
class PiStderr:
    """Drain one process's stderr in the background, keeping its last lines."""

    __slots__ = ("_drain", "_tail")
    _tail: deque[str]
    _drain: asyncio.Task[None]

    # None is asyncio's own "no pipe" for Process.stderr: nothing to drain.
    def __new__(cls, stream: asyncio.StreamReader | None) -> Self:
        """Start draining *stream*; ``None`` (no pipe) drains nothing."""
        self = super().__new__(cls)
        self._tail = deque(maxlen=_TAIL_LINES)
        self._drain = asyncio.ensure_future(self._read(stream))
        return self

    def tail(self) -> str:
        """Return the last few lines read, joined, for diagnostics only."""
        return " | ".join(self._tail) or "(no stderr)"

    async def settle(self) -> None:
        """Give the drain a bounded moment to read pi's last lines.

        Bounded: a process that stopped but keeps stderr open must not hang
        the call.
        """
        await asyncio.wait({self._drain}, timeout=_SETTLE_S)

    async def stop(self) -> None:
        """Settle, then stop the drain; a failure in it is logged, never raised."""
        await self.settle()
        if not self._drain.done():
            self._drain.cancel()
        try:
            await self._drain
        except asyncio.CancelledError:
            if not self._drain.cancelled():
                raise
        except Exception:
            logger.exception("call agent stderr drain failed")

    async def _read(self, stream: asyncio.StreamReader | None) -> None:
        """Read until pi closes stderr.

        An over-long line is skipped, never fatal: a drain that stopped
        reading would bring back the very deadlock it exists to prevent.
        """
        if stream is None:
            return
        while True:
            try:
                raw = await stream.readline()
            except ValueError:
                logger.warning("call agent wrote an over-long stderr line; skipped")
                continue
            if not raw:
                return
            self._tail.append(raw.decode(errors="replace").rstrip())
