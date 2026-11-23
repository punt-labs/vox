from __future__ import annotations

import asyncio
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest

from punt_vox.voxd.voice_agent.session import Conversation
from punt_vox.voxd.voice_call.progress import (
    CallProgress,
    ProgressEvents,
)
from punt_vox.voxd.voice_call.progress_types import WorkerProgress


async def test_fanout_bounded_and_unsubscribed() -> None:
    events = ProgressEvents()
    event = WorkerProgress("w", 1, "finished", "done")
    events.publish(event)
    async with events.subscribe() as first, events.subscribe() as second:
        assert first.empty()
        for seq in range(ProgressEvents.LIMIT + 2):
            events.publish(WorkerProgress("w", seq, "finished", "done"))
        assert first.qsize() == second.qsize() == ProgressEvents.LIMIT
        assert (await first.get()).seq == 2
    events.publish(event)


async def test_context_immediate_results_wait_decisions_bypass_lull() -> None:
    events = ProgressEvents()
    progress = CallProgress(events)
    conv = AsyncMock(spec=Conversation)
    with patch.object(CallProgress, "LULL", 0.08):
        task = asyncio.create_task(progress.run(cast("Conversation", conv)))
        await asyncio.sleep(0)
        events.publish(WorkerProgress("w", 1, "finished", "done"))
        await asyncio.sleep(0.02)
        assert conv.send_context.await_count == 1
        conv.send_text.assert_not_awaited()
        progress.activity()
        events.publish(
            WorkerProgress(
                "blocked", 2, "needs_decision", "Allow?", "permission_prompt"
            )
        )
        await asyncio.sleep(0.02)
        assert conv.send_context.await_count == 2
        assert conv.send_text.await_count == 1
        assert "needs_decision" in conv.send_text.call_args.args[0]
        await asyncio.sleep(0.09)
        assert conv.send_text.await_count == 2
        assert "finished" in conv.send_text.call_args.args[0]
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_empty_source_cancellation_and_send_failure() -> None:
    events = ProgressEvents()
    conv = AsyncMock(spec=Conversation)
    task = asyncio.create_task(CallProgress(events).run(cast("Conversation", conv)))
    await asyncio.sleep(0)
    conv.send_context.assert_not_awaited()
    conv.send_context.side_effect = OSError("closed")
    events.publish(WorkerProgress("w", 1, "finished", "done"))
    with pytest.raises(OSError, match="closed"):
        await task
    events.publish(WorkerProgress("w", 2, "finished", "no subscriber"))


async def test_activity_during_pending_wait_restarts_the_lull() -> None:
    events = ProgressEvents()
    progress = CallProgress(events)
    conv = AsyncMock(spec=Conversation)
    with patch.object(CallProgress, "LULL", 0.2):
        task = asyncio.create_task(progress.run(cast("Conversation", conv)))
        try:
            await asyncio.sleep(0)
            events.publish(WorkerProgress("w", 1, "finished", "done"))
            await asyncio.sleep(0.12)
            progress.activity()
            await asyncio.sleep(0.12)
            conv.send_text.assert_not_awaited()
            await asyncio.sleep(0.12)
            conv.send_text.assert_awaited_once()
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
