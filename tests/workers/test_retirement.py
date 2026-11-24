from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from threading import Event
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, Mock, patch

import pytest
from test_workers import FakeWorkerProvider, TrivialProfile, _registry

from punt_vox.voxd.hook_context_store import HookContextStore
from punt_vox.voxd.workers.actor import WorkerActor
from punt_vox.voxd.workers.inventory import WorkerInventory
from punt_vox.voxd.workers.policy import WorkerRepoPolicy
from punt_vox.voxd.workers.records import WorkerRecords
from punt_vox.voxd.workers.retirement import WorkerRetirement
from punt_vox.voxd.workers.settlement import WorkerSettlement
from punt_vox.voxd.workers.worktrees import WorkerWorktrees

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from punt_vox.voxd.workers.model import WorkerRecord
    from punt_vox.voxd.workers.registry import WorkerRegistry


def _settled(registry: WorkerRegistry) -> None:
    """Wait for the background settlement jobs to finish."""
    deadline = time.monotonic() + 30
    while not registry.cleanup_idle():
        assert time.monotonic() < deadline, f"never settled: {registry.list()}"
        time.sleep(0.01)


def test_successful_stop_forgets_finished_cleanup_job(tmp_path: Path) -> None:
    registry = _registry(tmp_path, FakeWorkerProvider())
    record = registry.start("work", str(tmp_path), provider="fake", harness="test")

    def check_durable_intent(_path: Path) -> None:
        [saved] = WorkerRecords(tmp_path / "workers").load()
        assert saved.state == "stopped"
        assert saved.last_report["cleanup_pending"] is True

    with patch.object(TrivialProfile, "cleanup", side_effect=check_durable_intent):
        stopped = registry.stop(record.id)

    assert stopped.state == "stopped"
    assert "cleanup_pending" not in stopped.last_report
    assert registry.cleanup_idle()
    registry.shutdown()


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
    updated = Event()
    late = replace(record, last_report={"cleanup_pending": True, "worker_seq": 8})
    outcomes = []
    inventory = Mock(spec=WorkerInventory)
    inventory.get.return_value = record

    def publish(_worker_id: str, merge: Callable[[WorkerRecord], WorkerRecord]) -> None:
        outcomes.append(merge(late).last_report)
        updated.set()

    inventory.update.side_effect = publish
    retirement = WorkerRetirement(
        WorkerSettlement({"test": profile}, Mock(spec=WorkerRepoPolicy)), inventory
    )
    retirement.schedule(record.id)
    try:
        assert started.wait(1)
        inventory.update.assert_not_called()
    finally:
        release.set()

    assert updated.wait(1)
    assert outcomes == [{"worker_seq": 8}]
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
            live = registry.start(
                "live", str(tmp_path), provider="fake", harness="test"
            )
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


async def test_cleanup_completion_checkpoints_without_another_worker_request(
    tmp_path: Path,
) -> None:
    provider = FakeWorkerProvider()
    registry = _registry(tmp_path, provider)
    worker = registry.start("done", str(tmp_path), provider="fake", harness="test")
    release = Event()

    def hold_cleanup(_path: Path) -> None:
        assert release.wait(3)

    with patch.object(TrivialProfile, "cleanup", side_effect=hold_cleanup):
        try:
            provider.alive = False
            assert registry.list()[0].state == "exited"
            store = HookContextStore()
            actor = WorkerActor(registry, store)
            await actor.hooks(store).ingest(
                {
                    "session_id": worker.id,
                    "cwd": worker.worktree,
                    "hook_event_name": "Stop",
                    "last_assistant_message": "finished",
                    "relay_seq": 1,
                },
                worker_only=True,
            )
            records = WorkerRecords(tmp_path / "workers")
            assert records.load()[0].last_report["cleanup_pending"] is True
            release.set()
            async with asyncio.timeout(1):
                while "cleanup_pending" in (saved := records.load()[0]).last_report:
                    await asyncio.sleep(0.01)
            assert saved.last_report["last_assistant_message"] == "finished"
        finally:
            release.set()


def test_discard_retried_during_a_cleanup_only_job_is_not_lost(
    tmp_path: Path,
) -> None:
    """A stop that queues a discard while harness cleanup runs still gets it.

    The record carries both a failed cleanup and a failed discard. A restore
    queues a cleanup-only job; a stop during that job marks the discard
    pending again. The job must write back only the cleanup markers and then
    settle the newly queued discard, never fold the stale discard error over
    the retry.
    """
    provider = FakeWorkerProvider()
    provider.fail = True
    worktrees = MagicMock(spec=WorkerWorktrees)
    first = _registry(tmp_path, provider, worktrees)
    with pytest.raises(OSError, match="launch failed"):
        first.start("edit", str(tmp_path), provider="fake", harness="test")
    _settled(first)
    first.shutdown()
    launched = first.list()[0]
    assert launched.state == "stopped"
    records = WorkerRecords(tmp_path / "workers")
    records.save(
        replace(
            launched,
            last_report={
                "launch_error": launched.last_report["launch_error"],
                "cleanup_error": "TimeoutError",
                "discard_error": "rmtree: OSError: busy",
            },
        )
    )
    worktrees.discard.reset_mock()
    started, release = Event(), Event()

    def hold_cleanup(_path: Path) -> None:
        started.set()
        assert release.wait(3)

    with patch.object(TrivialProfile, "cleanup", side_effect=hold_cleanup):
        restored = _registry(tmp_path, provider, worktrees)
        try:
            assert started.wait(1)
            during = restored.stop(launched.id)
            assert during.last_report["discard_pending"] is True
        finally:
            release.set()
        _settled(restored)
    done = restored.list()[0]
    assert "cleanup_error" not in done.last_report
    assert not any(k.startswith("discard_") for k in done.last_report)
    assert done.last_report["worktree_discarded"] is True
    worktrees.discard.assert_called_once()
