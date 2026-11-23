from __future__ import annotations

import asyncio
from dataclasses import replace
from threading import Event
from typing import TYPE_CHECKING
from unittest.mock import Mock, patch

from test_workers import FakeWorkerProvider, TrivialProfile, _registry

from punt_vox.voxd.hook_context_store import HookContextStore
from punt_vox.voxd.workers.actor import WorkerActor
from punt_vox.voxd.workers.retirement import WorkerRetirement

if TYPE_CHECKING:
    from pathlib import Path


def test_terminal_cleanup_does_not_block_status_or_replace_late_report(
    tmp_path: Path,
) -> None:
    provider = FakeWorkerProvider()
    record = (
        _registry(tmp_path, provider)
        .start("work", str(tmp_path), provider="fake", harness="test")
        .retire()
    )
    started, release = Event(), Event()

    def wait_for_release(_path: Path) -> None:
        started.set()
        assert release.wait(2)

    profile = Mock(spec=TrivialProfile)
    profile.cleanup.side_effect = wait_for_release
    retirement = WorkerRetirement({"test": profile})
    retirement.schedule(record)
    try:
        assert started.wait(1)
        assert retirement.status(record) == record
    finally:
        release.set()

    late = replace(record, last_report={"cleanup_pending": True, "worker_seq": 8})
    assert retirement.finish(late).last_report == {"worker_seq": 8}
    profile.cleanup.assert_called_once()


async def test_stop_does_not_hold_hook_lock_while_retirement_cleanup_waits(
    tmp_path: Path,
) -> None:
    provider = FakeWorkerProvider()
    registry = _registry(tmp_path, provider)
    worker = registry.start("done", str(tmp_path), provider="fake", harness="test")
    started, release = Event(), Event()

    def hold_cleanup(_path: Path) -> None:
        started.set()
        assert release.wait(3)

    with patch.object(TrivialProfile, "cleanup", side_effect=hold_cleanup):
        try:
            provider.alive = False
            assert registry.list()[0].state == "exited"
            assert await asyncio.to_thread(started.wait, 1)
            provider.alive = True
            live = registry.start("live", str(tmp_path), provider="fake", harness="test")
            store = HookContextStore()
            actor = WorkerActor(registry, store)
            async with asyncio.timeout(1):
                await actor.stop(worker.id)
                await actor.hooks(store).ingest(
                    {
                        "session_id": live.id,
                        "cwd": live.worktree,
                        "hook_event_name": "SessionStart",
                        "relay_seq": 1,
                    },
                    worker_only=True,
                )
            assert store.checkpoint(live.id) is not None
        finally:
            release.set()
