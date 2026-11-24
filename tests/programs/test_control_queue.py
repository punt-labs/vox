"""Tests for FIFO admission while playback is off."""

from __future__ import annotations

from punt_vox.voxd.programs import Program, ProgramState
from punt_vox.voxd.programs.control_queue import ControlQueue
from punt_vox.voxd.programs.lifecycle_signal import TurnOn
from punt_vox.voxd.programs.playback_signal import Rotate, StepBack, StepForward
from punt_vox.voxd.programs.rotate_policy import RotatePolicy


async def test_only_pending_starts_admit_steps_while_off() -> None:
    idle = Program(ProgramState.initial(), RotatePolicy())
    queue = ControlQueue()
    rotate = Rotate()
    first, second = TurnOn(), TurnOn()
    step = StepForward()
    assert queue.post(idle, rotate)
    assert not queue.post(idle, step)
    assert queue.post(idle, first)
    assert queue.post(idle, second)
    assert await queue.get() is rotate
    queue.task_done()
    assert await queue.get() is first
    queue.task_done()
    assert queue.post(idle, step)
    assert await queue.get() is second
    queue.task_done()
    assert not queue.post(idle, StepBack())
    assert await queue.get() is step
    queue.task_done()
    await queue.join()
