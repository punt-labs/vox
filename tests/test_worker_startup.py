from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING
from unittest.mock import patch

from test_workers import FakeWorkerProvider, _registry

from punt_vox.voxd.hook_context_store import HookContextStore
from punt_vox.voxd.workers.records import WorkerRecords
from punt_vox.voxd.workers.startup import WorkerStartup
from punt_vox.voxd.workers.tmux import TmuxWorkerProvider

if TYPE_CHECKING:
    from pathlib import Path


def test_startup_does_not_retire_worker_when_provider_is_unavailable(
    tmp_path: Path,
) -> None:
    registry = _registry(tmp_path, FakeWorkerProvider())
    worker = registry.start("work", str(tmp_path), provider="fake", harness="test")
    registered = replace(worker, provider="tmux")
    records = WorkerRecords(tmp_path / "workers")
    records.save(registered)
    with patch.object(TmuxWorkerProvider, "status", side_effect=OSError):
        WorkerStartup.run(records, HookContextStore(), TmuxWorkerProvider())

    assert records.load() == [registered]
