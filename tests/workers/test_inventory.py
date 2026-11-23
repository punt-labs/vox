from __future__ import annotations

from dataclasses import replace
from threading import Event, Thread
from typing import TYPE_CHECKING
from unittest.mock import patch

from test_workers import FakeWorkerProvider, _registry

from punt_vox.voxd.workers.inventory import WorkerInventory
from punt_vox.voxd.workers.records import WorkerRecords

if TYPE_CHECKING:
    from pathlib import Path


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
