"""FIFO command admission for the single playback writer."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Self, final

from punt_vox.types_programs.mode import Mode
from punt_vox.voxd.programs.program import Program
from punt_vox.voxd.programs.step_admission import StepAdmission

if TYPE_CHECKING:
    from punt_vox.voxd.programs.control_signal import ControlSignal
    from punt_vox.voxd.programs.playback_source import PlaybackSource

__all__ = ["ControlQueue"]


@final
class ControlQueue:
    """Admit steps against the source projected at the tail of the queue."""

    __slots__ = ("_admission", "_queue")
    _admission: StepAdmission
    _queue: asyncio.Queue[ControlSignal]

    def __new__(cls) -> Self:
        """Create an empty queue with no pending source changes."""
        self = super().__new__(cls)
        self._queue = asyncio.Queue()
        self._admission = StepAdmission()
        return self

    @staticmethod
    def is_off(source: PlaybackSource) -> bool:
        """Return whether the current source is an off Program."""
        return isinstance(source, Program) and source.mode is Mode.OFF

    def post(self, source: PlaybackSource, signal: ControlSignal) -> bool:
        """Admit steps if the last queued start or stop projects an active source.

        With no queued source change, check the current source instead. True
        means the signal was enqueued, not that it moved the cursor.
        """
        if not self._admission.admit(signal, active=not self.is_off(source)):
            return False
        self._queue.put_nowait(signal)
        return True

    async def get(self) -> ControlSignal:
        """Forget a source change as the writer takes it for synchronous apply."""
        signal = await self._queue.get()
        self._admission.consume(signal)
        return signal

    def task_done(self) -> None:
        """Mark the writer's current command done."""
        self._queue.task_done()

    async def join(self) -> None:
        """Wait for all posted commands to finish."""
        await self._queue.join()
