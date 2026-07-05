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
    from punt_vox.voxd.programs.filler import Filler, FillPlanSource
    from punt_vox.voxd.programs.program import Program

__all__ = ["ControlChannel"]

logger = logging.getLogger(__name__)


@final
class ControlChannel:
    """Serialize every Program mutation through one consumer (single-writer).

    The consumer is also the orchestration point: after each applied command it
    reconciles the background fill to the Program's ``filling`` flag -- starting
    the :class:`Filler` on the active plan when generation is wanted, cancelling
    it otherwise -- so the fill lifecycle rides the single writer and never
    races the Program's state.
    """

    __slots__ = (
        "_changed",
        "_filler",
        "_interrupt",
        "_plan_source",
        "_program",
        "_queue",
    )
    _program: Program
    _queue: asyncio.Queue[ControlSignal]
    _changed: asyncio.Event
    _interrupt: asyncio.Event
    _filler: Filler | None
    _plan_source: FillPlanSource | None

    def __new__(cls, program: Program) -> Self:
        self = super().__new__(cls)
        self._program = program
        self._queue = asyncio.Queue()
        self._changed = asyncio.Event()
        self._interrupt = asyncio.Event()
        self._filler = None
        self._plan_source = None
        return self

    def attach_fill(self, filler: Filler, plan_source: FillPlanSource) -> None:
        """Wire the background fill so the consumer reconciles it after each apply.

        Separate from construction because the Filler needs this channel to post
        its outcomes -- the two are built, then joined here.
        """
        self._filler = filler
        self._plan_source = plan_source

    @property
    def program(self) -> Program:
        """Return the Program this channel is the sole writer to."""
        return self._program

    @property
    def changed(self) -> asyncio.Event:
        """Return the event set after each applied command (the loop races it)."""
        return self._changed

    @property
    def interrupt(self) -> asyncio.Event:
        """Return the event set when an interrupting command is applied.

        Set by skip / next / play-a-part / off, not by a retune or a fill
        outcome -- so the loop stops the current track at once for the former
        and finishes it first for the latter.
        """
        return self._interrupt

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
        self._reconcile_fill()
        if signal.interrupts:
            self._interrupt.set()
        self._changed.set()

    def _reconcile_fill(self) -> None:
        """Match the background fill to the Program's ``filling`` flag."""
        if self._filler is None or self._plan_source is None:
            return
        if self._program.state.filling:
            self._filler.ensure_running(self._plan_source.current_plan())
        else:
            self._filler.cancel()

    async def join(self) -> None:
        """Block until every posted command has been applied."""
        await self._queue.join()
