from __future__ import annotations

from dataclasses import replace
from subprocess import CompletedProcess
from typing import TYPE_CHECKING
from unittest.mock import patch

from test_workers import FakeWorkerProvider, _registry

from punt_vox.voxd.hook_context_store import HookContextStore
from punt_vox.voxd.workers.command import WorkerCommand
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


def test_startup_does_not_reap_live_worker_after_failed_tmux_probe(
    tmp_path: Path,
) -> None:
    registry = _registry(tmp_path, FakeWorkerProvider())
    worker = registry.start("work", str(tmp_path), provider="fake", harness="test")
    registered = replace(worker, provider="tmux")
    records = WorkerRecords(tmp_path / "workers")
    records.save(registered)
    with (
        patch.object(
            WorkerCommand,
            "run",
            side_effect=[
                CompletedProcess(
                    [], 1, "", "error connecting to tmux (Permission denied)"
                ),
                CompletedProcess([], 0, f"{worker.session}\n", ""),
                CompletedProcess(
                    [], 0, f"VOX_WORKER_OWNER={records.namespace}:{worker.id}\n", ""
                ),
            ],
        ),
        patch.object(TmuxWorkerProvider, "stop") as stop,
    ):
        WorkerStartup.run(records, HookContextStore(), TmuxWorkerProvider())

    stop.assert_not_called()
    assert records.load() == [registered]
