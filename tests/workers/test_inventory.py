from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

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
