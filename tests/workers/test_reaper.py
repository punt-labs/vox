from __future__ import annotations

from subprocess import CompletedProcess
from typing import TYPE_CHECKING
from unittest.mock import patch
from uuid import uuid4

from punt_vox.voxd.workers.reaper import WorkerReaper
from punt_vox.voxd.workers.records import WorkerRecords
from punt_vox.voxd.workers.tmux import TmuxWorkerProvider

if TYPE_CHECKING:
    from pathlib import Path


def test_reaper_keeps_worker_when_tmux_owner_cannot_be_verified(tmp_path: Path) -> None:
    records = WorkerRecords(tmp_path / "workers")
    worker = str(uuid4())
    session = f"vox-worker-{worker}"
    with (
        patch(
            "punt_vox.voxd.workers.reaper.WorkerCommand.run",
            side_effect=[
                CompletedProcess([], 0, session),
                CompletedProcess(
                    [], 1, f"VOX_WORKER_OWNER={records.namespace}:{worker}"
                ),
            ],
        ) as command,
        patch.object(TmuxWorkerProvider, "stop") as stop,
    ):
        WorkerReaper.run(records, TmuxWorkerProvider())

    assert command.call_count == 2
    stop.assert_not_called()
