from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import replace
from threading import Event, Thread
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, Mock, patch

import pytest
from test_workers import FakeWorkerProvider, TrivialProfile, _registry

from punt_vox.voxd.hook_context_store import HookContextStore
from punt_vox.voxd.workers.actor import WorkerActor
from punt_vox.voxd.workers.git_failure import DiscardError
from punt_vox.voxd.workers.inventory import WorkerInventory
from punt_vox.voxd.workers.policy import WorkerRepoPolicy
from punt_vox.voxd.workers.records import WorkerRecords
from punt_vox.voxd.workers.retirement import WorkerRetirement
from punt_vox.voxd.workers.settlement import SettledDebts, WorkerSettlement
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


def _raise(exc: Exception) -> None:
    raise exc


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


async def test_stop_returns_before_a_retried_discard_runs(tmp_path: Path) -> None:
    """A retried discard runs off the stop reply, which reports it pending.

    The client's stop budget is seconds; a discard is up to six git calls at
    20 s each. The reply carries discard_pending and the outcome lands on
    the record once the queue has run it.
    """
    provider = FakeWorkerProvider()
    provider.fail = True
    worktrees = MagicMock(spec=WorkerWorktrees)
    busy = DiscardError()
    busy.attempt("rmtree", lambda: _raise(OSError("busy")))
    worktrees.discard.side_effect = busy
    registry = _registry(tmp_path, provider, worktrees)
    with pytest.raises(OSError, match="launch failed"):
        registry.start("edit", str(tmp_path), provider="fake", harness="test")
    _settled(registry)
    failed = registry.list()[0]
    assert failed.last_report["discard_error"] == "rmtree: OSError: busy"
    release = Event()

    def blocking_discard(project: Path, worktree: Path, worker_id: str) -> None:
        assert release.wait(3)

    worktrees.discard.side_effect = blocking_discard
    actor = WorkerActor(registry)
    try:
        async with asyncio.timeout(1):
            reply = await actor.stop(failed.id)
    finally:
        release.set()
    assert reply["state"] == "stopped"
    assert reply["last_report"]["discard_pending"] is True
    _settled(registry)
    done = registry.list()[0]
    assert done.last_report["worktree_discarded"] is True
    assert "discard_pending" not in done.last_report
    assert worktrees.discard.call_count == 2


def test_unsaved_settlement_outcome_log_names_the_worker(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A save that fails after the irreversible discard is logged with the id.

    The discard already ran, so the record is the only trace of its outcome;
    the warning names the worker and the failure, not just the exception type.
    """
    record = (
        _registry(tmp_path, FakeWorkerProvider())
        .start("work", str(tmp_path), provider="fake", harness="test")
        .retire()
    )
    inventory = Mock(spec=WorkerInventory)
    inventory.get.return_value = record
    inventory.update.side_effect = OSError("disk full")
    retirement = WorkerRetirement(
        WorkerSettlement({"test": TrivialProfile()}, Mock(spec=WorkerRepoPolicy)),
        inventory,
    )
    with caplog.at_level(logging.WARNING, logger="punt_vox.voxd.workers.retirement"):
        job = retirement.schedule(record.id)
        assert job is not None
        job.result(timeout=1)
    [warning] = caplog.records
    assert record.id in warning.getMessage()
    assert "OSError: disk full" in warning.getMessage()


def test_discard_retried_while_a_job_winds_down_is_not_dropped(
    tmp_path: Path,
) -> None:
    """A retry between a job's last owed() check and its release still runs.

    The queue marks the worker active until the job's operation returns. A
    stop that re-queues the discard in that window used to have schedule()
    refused, leaving discard_pending on the record with no job to run it.
    """
    provider = FakeWorkerProvider()
    provider.fail = True
    worktrees = MagicMock(spec=WorkerWorktrees)
    busy = DiscardError()
    busy.attempt("rmtree", lambda: _raise(OSError("busy")))
    worktrees.discard.side_effect = busy
    settled_once, release = Event(), Event()
    original = WorkerRetirement._checkpoint

    def checkpoint_then_linger(
        self: WorkerRetirement, worker_id: str, settled: SettledDebts
    ) -> WorkerRecord:
        record = original(self, worker_id, settled)
        settled_once.set()
        assert release.wait(3)
        return record

    with patch.object(WorkerRetirement, "_checkpoint", checkpoint_then_linger):
        registry = _registry(tmp_path, provider, worktrees)
        with pytest.raises(OSError, match="launch failed"):
            registry.start("edit", str(tmp_path), provider="fake", harness="test")
        try:
            assert settled_once.wait(1)
            worktrees.discard.side_effect = None
            retried, job = registry.request_stop(registry.list()[0].id)
            assert retried.last_report["discard_pending"] is True
            assert job is None
        finally:
            release.set()
        _settled(registry)
    done = registry.list()[0]
    assert done.last_report["worktree_discarded"] is True
    assert "discard_pending" not in done.last_report
    assert worktrees.discard.call_count == 2


async def test_another_workers_discard_does_not_delay_a_stop(tmp_path: Path) -> None:
    """Worker B's stop settles its harness cleanup while worker A's discard runs.

    A discard is up to six git calls at 20 s each; a stop reply has 30 s.
    Each worker's cleanup runs on its own lane, so B's reply never waits
    behind A's discard.
    """
    provider = FakeWorkerProvider()
    worktrees = MagicMock(spec=WorkerWorktrees)
    discarding, release = Event(), Event()

    def blocking_discard(project: Path, worktree: Path, worker_id: str) -> None:
        discarding.set()
        assert release.wait(5)

    worktrees.discard.side_effect = blocking_discard
    registry = _registry(tmp_path, provider, worktrees)
    provider.fail = True
    with pytest.raises(OSError, match="launch failed"):
        registry.start("doomed", str(tmp_path), provider="fake", harness="test")
    provider.fail = False
    try:
        assert await asyncio.to_thread(discarding.wait, 1)
        live = registry.start("live", str(tmp_path), provider="fake", harness="test")
        actor = WorkerActor(registry)
        async with asyncio.timeout(1):
            reply = await actor.stop(live.id)
    finally:
        release.set()
    assert reply["state"] == "stopped"
    assert "cleanup_pending" not in reply["last_report"]
    _settled(registry)


def test_unsaved_settlement_outcome_still_reaches_memory(tmp_path: Path) -> None:
    """A save that fails after the discard still updates the in-memory record.

    The discard is irreversible; a record that still says discard_pending
    after it ran would queue the discard again on the next stop.
    """
    launched = _registry(tmp_path, FakeWorkerProvider()).start(
        "work", str(tmp_path), provider="fake", harness="test"
    )
    record = replace(launched, state="stopped", last_report={"discard_pending": True})
    inventory = WorkerInventory(WorkerRecords(tmp_path / "workers"))
    inventory.restore([record])

    def discarded(current: WorkerRecord) -> WorkerRecord:
        return replace(
            current, last_report={**current.last_report, "worktree_discarded": True}
        )

    policy = Mock(spec=WorkerRepoPolicy)
    policy.discard_recorded.side_effect = discarded
    retirement = WorkerRetirement(
        WorkerSettlement({"test": TrivialProfile()}, policy), inventory
    )
    with patch.object(WorkerRecords, "save", side_effect=OSError("disk full")):
        job = retirement.schedule(record.id)
        assert job is not None
        job.result(timeout=1)
    remembered = inventory.get(record.id)
    assert remembered.last_report["worktree_discarded"] is True
    assert "discard_pending" not in remembered.last_report


async def test_a_second_stop_on_the_same_worker_never_grows_the_awaited_job(
    tmp_path: Path,
) -> None:
    """Reply A waits on its cleanup job; a discard a later stop marks runs apart.

    While job A runs harness cleanup, a second stop on the same worker marks
    the discard pending. That discard is up to six git calls; it must run as
    its own job after A ends, never inside the future reply A is awaiting.
    """
    provider = FakeWorkerProvider()
    worktrees = MagicMock(spec=WorkerWorktrees)
    discarding, discard_release = Event(), Event()

    def blocking_discard(project: Path, worktree: Path, worker_id: str) -> None:
        discarding.set()
        assert discard_release.wait(5)

    worktrees.discard.side_effect = blocking_discard
    registry = _registry(tmp_path, provider, worktrees)
    worker = registry.start("work", str(tmp_path), provider="fake", harness="test")
    cleaning, cleanup_release = Event(), Event()

    def hold_cleanup(_path: Path) -> None:
        cleaning.set()
        assert cleanup_release.wait(5)

    actor = WorkerActor(registry)
    with patch.object(TrivialProfile, "cleanup", side_effect=hold_cleanup):
        first = asyncio.create_task(actor.stop(worker.id))
        try:
            assert await asyncio.to_thread(cleaning.wait, 1)
            registry.hook_records.update(
                worker.id,
                lambda r: replace(
                    r, last_report={**r.last_report, "discard_error": "rmtree: busy"}
                ),
            )
            second, job = registry.request_stop(worker.id)
            assert second.last_report["discard_pending"] is True
            assert job is None
            cleanup_release.set()
            async with asyncio.timeout(1):
                reply = await first
        finally:
            cleanup_release.set()
            discard_release.set()
    assert "cleanup_pending" not in reply["last_report"]
    assert not discarding.is_set() or reply["last_report"].get("discard_pending")
    _settled(registry)
    assert registry.list()[0].last_report["worktree_discarded"] is True


def test_failed_settlement_save_never_overwrites_a_concurrent_stop(
    tmp_path: Path,
) -> None:
    """A stop that lands while a failed save is remembered keeps its marker.

    The settlement job folds worktree_discarded; its save fails. A stop on
    the same worker marks discard_pending in that moment. Memory must end
    with both: the stop applied on top of the fold, never a stale fold
    written over the stop. Otherwise the queue's rerun finds nothing owed
    and the retry is lost until restart.
    """
    launched = _registry(tmp_path, FakeWorkerProvider()).start(
        "work", str(tmp_path), provider="fake", harness="test"
    )
    record = replace(launched, state="stopped", last_report={"discard_pending": True})
    inventory = WorkerInventory(WorkerRecords(tmp_path / "workers"))
    inventory.restore([record])

    def discarded(current: WorkerRecord) -> WorkerRecord:
        return replace(
            current, last_report={**current.last_report, "worktree_discarded": True}
        )

    policy = Mock(spec=WorkerRepoPolicy)
    policy.discard_recorded.side_effect = discarded
    retirement = WorkerRetirement(
        WorkerSettlement({"test": TrivialProfile()}, policy), inventory
    )
    original_remember = WorkerInventory.remember
    stopper = Thread(
        target=lambda: inventory.update(
            record.id,
            lambda r: replace(
                r, last_report={**r.last_report, "discard_pending": True}
            ),
        )
    )

    def remember_while_a_stop_lands(self: WorkerInventory, rec: WorkerRecord) -> None:
        if not stopper.is_alive() and stopper.ident is None:
            stopper.start()
            time.sleep(0.05)
        original_remember(self, rec)

    with (
        patch.object(WorkerRecords, "save", side_effect=[OSError("disk full"), None]),
        patch.object(WorkerInventory, "remember", remember_while_a_stop_lands),
    ):
        job = retirement.schedule(record.id)
        assert job is not None
        job.result(timeout=1)
        stopper.join(1)
    assert not stopper.is_alive()
    final = inventory.get(record.id).last_report
    assert final["worktree_discarded"] is True
    assert final["discard_pending"] is True
