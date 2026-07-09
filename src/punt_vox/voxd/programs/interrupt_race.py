"""Race a player process's natural end against a control interrupt.

``InterruptRace`` decides how a playing track *stopped* and returns a
:class:`TrackEnd`: a user interrupt (skip / off / play-a-part) wins outright; a
*raised* ``wait`` is a player error -- retrieved and logged here so it never leaks
as an unretrieved exception nor masquerades as a normal advance -- and both make
the loop kill without advancing. Otherwise the player's ``wait`` settled with an
exit code: 0 is a clean natural end (advance); non-zero is a fault the loop
records on ``PlaybackHealth`` before advancing. Extracting it keeps
:class:`ProgramLoop` focused on *what to do*, with the race mechanics owned here.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Any, Self, final

from punt_vox.voxd.programs.track_end import TrackEnd

if TYPE_CHECKING:
    from punt_vox.voxd.programs.player import PlayerProcess

__all__ = ["InterruptRace"]

logger = logging.getLogger(__name__)


@final
class InterruptRace:
    """Settle how a track stopped: interrupt, player error, clean exit, or fault."""

    __slots__ = ("_interrupt",)
    _interrupt: asyncio.Event

    def __new__(cls, interrupt: asyncio.Event) -> Self:
        self = super().__new__(cls)
        self._interrupt = interrupt
        return self

    async def settle(self, proc: PlayerProcess) -> TrackEnd:
        """Return the :class:`TrackEnd` describing how ``proc`` stopped.

        A user interrupt wins outright (``interrupted``). Otherwise the player's
        ``wait`` settled: a *raised* wait is a player error -- retrieved and logged
        so it never leaks nor masquerades as a clean advance -- and is reported as
        ``interrupted`` too (kill, do not advance). A clean settle carries the exit
        code, so the loop tells a clean end (0) from a fault (non-zero, F3).
        """
        wait_task = asyncio.ensure_future(proc.wait())
        interrupt_task = asyncio.ensure_future(self._interrupt.wait())
        done, pending = await asyncio.wait(
            {wait_task, interrupt_task}, return_when=asyncio.FIRST_COMPLETED
        )
        await self._cancel_all(pending)
        exit_code = self._exit_code(wait_task) if wait_task in done else None
        if interrupt_task in done or exit_code is None:
            return TrackEnd(interrupted=True, exit_code=None)
        return TrackEnd(interrupted=False, exit_code=exit_code)

    @staticmethod
    async def _cancel_all(tasks: set[asyncio.Task[Any]]) -> None:
        # Task[Any]: the race mixes a Task[int] (proc.wait) and a Task[bool]
        # (Event.wait); this only cancels-and-awaits, so the result type is moot.
        """Cancel and reap the losing tasks of the interrupt race."""
        for task in tasks:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    @staticmethod
    def _exit_code(wait_task: asyncio.Task[int]) -> int | None:
        """Return the settled exit code, or ``None`` for a raised (errored) wait.

        ``None`` is the documented "player error" signal -- the raised wait is
        retrieved and logged here so it never leaks nor masquerades as a clean end.
        """
        exc = wait_task.exception()
        if exc is not None:
            logger.error("player wait failed; not a clean track end", exc_info=exc)
            return None
        return wait_task.result()
