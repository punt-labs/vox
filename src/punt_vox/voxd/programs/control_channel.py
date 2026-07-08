"""The single-writer control channel -- the one place the Program is mutated.

``ControlChannel`` is a single-consumer command queue. Every mutation of the
Program -- user commands, automatic advances, and fill outcomes -- is posted as
a :class:`ControlSignal` and drained one at a time by exactly one consumer task
that applies it to the Program. This realises the O2 concurrency contract: the Z
state machine is sequential, so two clients firing ``next`` and ``vibe`` at once
(or a fill completing mid-command) can never interleave or apply against a stale
base. A command whose precondition no longer holds -- a lost race, e.g. a
``rotate`` that arrives just after a ``turn_off`` -- raises a
:class:`GuardViolationError`; the consumer logs it at INFO and moves on rather than
dying, so one losing racer cannot take down the sole writer.

A *non-guard* failure is a different animal: a plain ``ValueError`` from a
corrupt successor state, or any other unexpected exception, means a bug -- not a
race. The consumer never swallows it as a lost race; it surfaces at ERROR
(vox-ig52) through the ``serve`` guard, always sets ``changed`` first so the
playback loop never blocks forever on a half-applied command, and keeps the sole
writer alive (a full restart-supervisor lands in slice 5).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.programs.control_signal import ControlSignal
from punt_vox.voxd.programs.guard import GuardViolationError

if TYPE_CHECKING:
    from punt_vox.voxd.programs.fill_reconciler import FillReconciler
    from punt_vox.voxd.programs.program import Program

__all__ = ["ControlChannel"]

logger = logging.getLogger(__name__)


@final
class ControlChannel:
    """Serialize every Program mutation through one consumer (single-writer).

    The consumer is also the orchestration point: after each applied command it
    hands the Program to its :class:`FillReconciler`, which starts or cancels the
    background fill to match the ``filling`` flag -- so the fill lifecycle rides
    the single writer and never races the Program's state.
    """

    __slots__ = (
        "_changed",
        "_interrupt",
        "_program",
        "_queue",
        "_reconciler",
    )
    _program: Program
    _queue: asyncio.Queue[ControlSignal]
    _changed: asyncio.Event
    _interrupt: asyncio.Event
    _reconciler: FillReconciler | None

    def __new__(cls, program: Program) -> Self:
        self = super().__new__(cls)
        self._program = program
        self._queue = asyncio.Queue()
        self._changed = asyncio.Event()
        self._interrupt = asyncio.Event()
        self._reconciler = None
        return self

    def attach_reconciler(self, reconciler: FillReconciler) -> None:
        """Wire the fill reconciler the consumer runs after each applied command.

        Separate from construction because the reconciler's Filler needs this
        channel to post its outcomes -- the two are built, then joined here.
        """
        self._reconciler = reconciler

    @property
    def program(self) -> Program:
        """Return the Program this channel is the sole writer to."""
        return self._program

    def retarget(self, program: Program) -> None:
        """Swap which Program the sole writer animates (a ``play <name>`` switch).

        Called only from inside an applied command (a ``SwitchProgram`` on the
        consumer thread), so the swap is serialised with every other mutation --
        the loop and the fill re-read :attr:`program` after the writer wakes them
        and see the new Program, never one half-swapped (the vox-73m5 hazard).
        """
        self._program = program

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
        """Drain and apply commands forever -- the sole writer to the Program.

        The top-level guard is the last line of defence: a bug in ``apply``
        (a corrupt-successor ``ValueError``, or anything else unexpected) is
        logged at ERROR and the loop continues, so the sole writer never dies
        silently on one bad command. ``apply_next`` has already set ``changed``
        in its ``finally``, so the playback loop is never left blocked.
        """
        while True:
            try:
                await self.apply_next()
            except Exception:
                logger.exception("control writer: unexpected error applying a command")

    async def apply_next(self) -> None:
        """Apply exactly one queued command, serializing all mutation.

        A :class:`GuardViolationError` (a lost race whose precondition no longer
        holds) is logged and swallowed so the single writer survives it. Any
        other failure propagates to the ``serve`` guard -- but the ``finally``
        first drains the queue, reconciles the fill, and *always* sets
        ``changed`` so a crash can never leave the loop blocked.
        """
        signal = await self._queue.get()
        try:
            self._apply_one(signal)
        finally:
            self._queue.task_done()
            try:
                self._reconcile_fill()
            finally:
                # The wake is unconditional: even a raising reconcile must not
                # leave the playback loop blocked on ``changed``.
                self._mark_applied(signal)

    def _apply_one(self, signal: ControlSignal) -> None:
        """Apply one command, swallowing only a benign lost-race guard."""
        try:
            signal.apply(self._program)
        except GuardViolationError:
            # A losing racer, not a bug -- log the mode + signal for the client
            # trail (a per-command applied/rejected result lands in slice 4).
            logger.info(
                "control signal rejected as a lost race in mode %s: %r",
                self._program.mode.value,
                signal,
            )

    def _mark_applied(self, signal: ControlSignal) -> None:
        """Wake the loop, interrupting the current track if the command demands it."""
        if signal.interrupts:
            self._interrupt.set()
        self._changed.set()

    def _reconcile_fill(self) -> None:
        """Reconcile the background fill to the Program via the wired reconciler."""
        if self._reconciler is not None:
            self._reconciler.reconcile(self._program)

    async def join(self) -> None:
        """Block until every posted command has been applied."""
        await self._queue.join()
