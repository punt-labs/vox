"""FIFO command admission for the single playback writer."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Self, final

from punt_vox.types_programs.mode import Mode
from punt_vox.voxd.programs.lifecycle_signal import TurnOn
from punt_vox.voxd.programs.playback_signal import StepBack, StepForward
from punt_vox.voxd.programs.program import Program
from punt_vox.voxd.programs.select_signal import SwitchSelection
from punt_vox.voxd.programs.switch_signal import SwitchProgram

if TYPE_CHECKING:
    from punt_vox.voxd.programs.control_signal import ControlSignal
    from punt_vox.voxd.programs.playback_source import PlaybackSource

__all__ = ["ControlQueue"]

_START_SIGNALS = (SwitchProgram, SwitchSelection, TurnOn)


@final
class ControlQueue:
    """Keep later steps behind starts the writer has yet to process."""

    __slots__ = ("_pending_starts", "_queue")
    _pending_starts: int
    _queue: asyncio.Queue[ControlSignal]

    def __new__(cls) -> Self:
        """Create an empty queue with no pending starts."""
        self = super().__new__(cls)
        self._queue = asyncio.Queue()
        self._pending_starts = 0
        return self

    @staticmethod
    def is_off(source: PlaybackSource) -> bool:
        """Return whether the current source is an off Program."""
        return isinstance(source, Program) and source.mode is Mode.OFF

    def post(self, source: PlaybackSource, signal: ControlSignal) -> bool:
        """Reject steps while off unless a start is already ahead in the FIFO.

        Return True on admission, not on application or cursor movement.
        """
        if (
            isinstance(signal, (StepBack, StepForward))
            and self.is_off(source)
            and not self._pending_starts
        ):
            return False
        self._queue.put_nowait(signal)
        self._pending_starts += isinstance(signal, _START_SIGNALS)
        return True

    async def get(self) -> ControlSignal:
        """Forget a start as the writer takes it; applying it does not yield."""
        signal = await self._queue.get()
        self._pending_starts -= isinstance(signal, _START_SIGNALS)
        return signal

    def task_done(self) -> None:
        """Mark the writer's current command done."""
        self._queue.task_done()

    async def join(self) -> None:
        """Wait for all posted commands to finish."""
        await self._queue.join()
