"""Race a player process's natural end against a control interrupt.

``InterruptRace`` decides how a playing track *stopped*: a user interrupt (skip /
off / play-a-part) wins outright; otherwise the player's ``wait`` settled, and a
clean exit is a natural end (advance) while a *raised* ``wait`` is a player error
-- retrieved and logged here so it never leaks as an unretrieved exception nor
masquerades as a normal advance. Extracting it keeps :class:`ProgramLoop` focused
on *when* to play and advance, with the race mechanics owned here.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Self, final

if TYPE_CHECKING:
    from punt_vox.voxd.programs.player import PlayerProcess

__all__ = ["InterruptRace"]

logger = logging.getLogger(__name__)


@final
class InterruptRace:
    """Decide whether a track ended by interrupt/player-error vs. a clean end."""

    __slots__ = ("_interrupt",)
    _interrupt: asyncio.Event

    def __new__(cls, interrupt: asyncio.Event) -> Self:
        self = super().__new__(cls)
        self._interrupt = interrupt
        return self

    async def interrupted(self, proc: PlayerProcess) -> bool:
        """Return True if the track did not end cleanly (interrupt or player error).

        A user interrupt wins outright. Otherwise the player's ``wait`` settled: a
        clean exit is a natural end (advance), but a *raised* ``wait`` is a player
        error -- retrieved and logged here so it never leaks nor masquerades as a
        normal advance.
        """
        wait_task = asyncio.ensure_future(proc.wait())
        interrupt_task = asyncio.ensure_future(self._interrupt.wait())
        done, pending = await asyncio.wait(
            {wait_task, interrupt_task}, return_when=asyncio.FIRST_COMPLETED
        )
        await self._cancel_all(pending)
        player_errored = wait_task in done and self._player_errored(wait_task)
        return interrupt_task in done or player_errored

    @staticmethod
    async def _cancel_all(tasks: set[asyncio.Task[int]]) -> None:
        """Cancel and reap the losing tasks of the interrupt race."""
        for task in tasks:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    @staticmethod
    def _player_errored(wait_task: asyncio.Task[int]) -> bool:
        """Retrieve the settled ``wait``; a raised one is an error, not a clean end."""
        exc = wait_task.exception()
        if exc is None:
            return False
        logger.error("player wait failed; not a clean track end", exc_info=exc)
        return True
