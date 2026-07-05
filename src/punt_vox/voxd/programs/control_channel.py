"""The single-writer control channel -- the one place the Program is mutated.

``ControlChannel`` is a single-consumer command queue. Every mutation of the
Program -- user commands, automatic advances, and fill outcomes -- is posted as
a :class:`ControlSignal` and drained one at a time by exactly one consumer task
that applies it to the Program. This realises the O2 concurrency contract: the Z
state machine is sequential, so two clients firing ``next`` and ``vibe`` at once
(or a fill completing mid-command) can never interleave or apply against a stale
base. A command whose precondition no longer holds -- a lost race, e.g. a
``rotate`` that arrives just after a ``turn_off`` -- raises the Z guard
``ValueError``; the consumer logs it and moves on rather than dying, so one
losing racer cannot take down the sole writer.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.programs.control_signal import ControlSignal

if TYPE_CHECKING:
    from punt_vox.voxd.programs.program import Program

__all__ = ["ControlChannel"]

logger = logging.getLogger(__name__)


@final
class ControlChannel:
    """Serialize every Program mutation through one consumer (single-writer)."""

    __slots__ = ("_changed", "_program", "_queue")
    _program: Program
    _queue: asyncio.Queue[ControlSignal]
    _changed: asyncio.Event

    def __new__(cls, program: Program) -> Self:
        self = super().__new__(cls)
        self._program = program
        self._queue = asyncio.Queue()
        self._changed = asyncio.Event()
        return self

    @property
    def program(self) -> Program:
        """Return the Program this channel is the sole writer to."""
        return self._program

    @property
    def changed(self) -> asyncio.Event:
        """Return the event set after each applied command (the loop races it)."""
        return self._changed

    def post(self, signal: ControlSignal) -> None:
        """Enqueue a command for the single consumer to apply."""
        self._queue.put_nowait(signal)

    async def serve(self) -> None:
        """Drain and apply commands forever -- the sole writer to the Program."""
        while True:
            await self.apply_next()

    async def apply_next(self) -> None:
        """Apply exactly one queued command, serializing all mutation.

        A guard ``ValueError`` (a lost race whose precondition no longer holds)
        is logged and swallowed so the single writer survives it.
        """
        signal = await self._queue.get()
        try:
            signal.apply(self._program)
        except ValueError:
            logger.info("control signal rejected as a lost race: %r", signal)
        finally:
            self._queue.task_done()
        self._changed.set()

    async def join(self) -> None:
        """Block until every posted command has been applied."""
        await self._queue.join()
