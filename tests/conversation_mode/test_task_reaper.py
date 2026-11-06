"""Tests for :class:`TaskReaper`: stop a child task, absorbing only its own cancel."""

from __future__ import annotations

import asyncio

import pytest

from conversation_mode._cancellation import (
    cancelled_while_reaping,
    swallow_a_cancellation,
)
from punt_vox.voxd.conversation_mode.task_reaper import TaskReaper


async def _forever() -> None:
    await asyncio.Event().wait()


async def test_reaping_cancels_a_running_child() -> None:
    child = asyncio.ensure_future(_forever())
    await TaskReaper.reap(child)
    assert child.cancelled()


async def test_reaping_a_finished_child_is_a_no_op() -> None:
    child = asyncio.ensure_future(asyncio.sleep(0))
    await child
    await TaskReaper.reap(child)
    assert not child.cancelled()


async def test_a_child_s_own_failure_propagates() -> None:
    async def failing() -> None:
        raise RuntimeError("drain broke")

    child = asyncio.ensure_future(failing())
    await asyncio.sleep(0)
    with pytest.raises(RuntimeError, match="drain broke"):
        await TaskReaper.reap(child)


async def test_a_caller_cancelled_while_reaping_stays_cancelled() -> None:
    child = asyncio.ensure_future(_forever())
    runner = await cancelled_while_reaping(TaskReaper.reap(child))
    with pytest.raises(asyncio.CancelledError):
        await runner


async def test_an_earlier_swallowed_cancellation_is_not_raised_again() -> None:
    async def tidy_after_a_swallowed_cancel() -> str:
        await swallow_a_cancellation()
        await TaskReaper.reap(asyncio.ensure_future(_forever()))
        return "reaped"

    assert await asyncio.ensure_future(tidy_after_a_swallowed_cancel()) == "reaped"
