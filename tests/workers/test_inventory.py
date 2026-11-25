from __future__ import annotations

import asyncio
from dataclasses import replace
from threading import Event, Thread
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from test_workers import FakeWorkerProvider, _registry

from punt_vox.voxd.hook_context_store import HookContextStore
from punt_vox.voxd.voice_call.progress import ProgressEvents
from punt_vox.voxd.workers.hooks import WorkerHooks
from punt_vox.voxd.workers.inventory import WorkerInventory
from punt_vox.voxd.workers.records import WorkerRecords

if TYPE_CHECKING:
    from pathlib import Path

    from punt_vox.voxd.workers.model import WorkerRecord


def test_exited_worker_admits_only_terminal_hooks_from_its_worktree(
    tmp_path: Path,
) -> None:
    registry = _registry(tmp_path, FakeWorkerProvider())
    record = registry.start("work", str(tmp_path), provider="fake", harness="test")
    inventory = WorkerInventory(WorkerRecords(tmp_path / "workers"))
    inventory.restore([record])

    assert inventory.match(record.id, record.worktree, "SessionStart") == record
    inventory.close(record.id)
    assert inventory.match(record.id, record.worktree, "SessionStart") is None
    assert inventory.match(record.id, record.worktree, "Stop") == record
    assert inventory.match(record.id, record.worktree, "SessionEnd") == record
    assert inventory.match(record.id, str(tmp_path), "Stop") is None

    inventory.remember(replace(record, state="stopped"))
    assert inventory.match(record.id, record.worktree, "Stop") is None


def test_admission_stays_retryable_during_liveness_confirmation(
    tmp_path: Path,
) -> None:
    registry = _registry(tmp_path, FakeWorkerProvider())
    record = registry.start("work", str(tmp_path), provider="fake", harness="test")
    inventory = registry.hook_records
    inventory.restore([record], unconfirmed={record.id})
    confirming, confirmed = Event(), Event()
    original_confirm = WorkerInventory.confirm

    def confirm(target: WorkerInventory, worker_id: str) -> None:
        confirming.set()
        original_confirm(target, worker_id)
        confirmed.set()

    listing = Thread(target=registry.list, daemon=True)

    class Quarantine(set[str]):
        def __contains__(self, worker_id: object) -> bool:
            pending = super().__contains__(worker_id)
            if not confirming.is_set():
                listing.start()
                assert confirming.wait(1)
                confirmed.wait(0.1)
            return pending

    with (
        patch.object(WorkerInventory, "confirm", confirm),
        patch.object(inventory, "_unconfirmed", Quarantine({record.id})),
    ):
        try:
            assert inventory.admission(
                record.id, record.worktree, "UserPromptSubmit"
            ) == (False, True)
        finally:
            listing.join(1)
        assert confirmed.is_set()
        assert inventory.admission(record.id, record.worktree, "UserPromptSubmit") == (
            True,
            False,
        )


def test_failed_save_keeps_the_transformed_record_in_memory(tmp_path: Path) -> None:
    """An update whose save fails still leaves the transformed record in memory."""
    registry = _registry(tmp_path, FakeWorkerProvider())
    record = registry.start("work", str(tmp_path), provider="fake", harness="test")
    inventory = WorkerInventory(WorkerRecords(tmp_path / "workers"))
    inventory.restore([record])
    with (
        patch.object(WorkerRecords, "save", side_effect=OSError("disk full")),
        pytest.raises(OSError, match="disk full"),
    ):
        inventory.update(record.id, lambda r: replace(r, state="stopped"))
    assert inventory.get(record.id).state == "stopped"


def test_a_retried_update_after_a_failed_save_reaches_the_disk(
    tmp_path: Path,
) -> None:
    """Memory already holds the unsaved record; the retry must still write.

    After a failed save the record stays in memory, so a retried identical
    update would compare equal to memory and skip the write, reporting
    success while the disk lacks the checkpoint and a restart loses it.
    """
    registry = _registry(tmp_path, FakeWorkerProvider())
    record = registry.start("work", str(tmp_path), provider="fake", harness="test")
    inventory = WorkerInventory(WorkerRecords(tmp_path / "workers"))
    inventory.restore([record])
    original = WorkerRecords.save
    writes: list[str] = []

    def counting_save(self: WorkerRecords, rec: WorkerRecord) -> None:
        writes.append(rec.id)
        original(self, rec)

    def stop(current: WorkerRecord) -> WorkerRecord:
        return replace(current, state="stopped")

    with (
        patch.object(WorkerRecords, "save", side_effect=OSError("disk full")),
        pytest.raises(OSError, match="disk full"),
    ):
        inventory.update(record.id, stop)
    with patch.object(WorkerRecords, "save", counting_save):
        inventory.update(record.id, stop)
    assert writes == [record.id]
    [saved] = WorkerRecords(tmp_path / "workers").load()
    assert saved.state == "stopped"


async def test_redelivered_exit_checkpoint_reaches_the_disk_after_a_failed_save(
    tmp_path: Path,
) -> None:
    """The exit checkpoint a hook carried must land on disk on its redelivery.

    The first delivery's save fails; memory keeps the checkpoint. On
    redelivery of the same relay_seq the acknowledgement reads memory and
    sees the sequence already received, so the retried write must happen
    through the memory-equal path, not be skipped as a no-op.
    """
    provider = FakeWorkerProvider()
    provider.ready = True
    registry = _registry(tmp_path, provider)
    worker = registry.start("task", str(tmp_path), provider="fake", harness="test")
    provider.alive = False
    assert registry.list()[0].state == "exited"
    store = HookContextStore()
    hooks = WorkerHooks(store, registry, asyncio.Lock(), ProgressEvents())
    payload: dict[str, object] = {
        "session_id": worker.id,
        "cwd": worker.worktree,
        "relay_seq": 1,
        "hook_event_name": "Stop",
        "last_assistant_message": "done",
    }
    with (
        patch.object(WorkerRecords, "save", side_effect=OSError("disk full")),
        pytest.raises(OSError, match="disk full"),
    ):
        await hooks.ingest(payload, worker_only=True)
    assert registry.hook_records.get(worker.id).last_report.get("worker_seq") == 1
    await hooks.ingest(payload, worker_only=True)
    [saved] = WorkerRecords(tmp_path / "workers").load()
    assert saved.last_report.get("worker_seq") == 1
