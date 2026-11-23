"""Restore durable worker projections without replaying hooks."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING
from unittest.mock import Mock

from test_workers import FakeWorkerProvider, _registry

from punt_vox.voxd.hook_context_store import HookContextStore
from punt_vox.voxd.hook_session_context import SessionContext
from punt_vox.voxd.workers.recovery import WorkerRecovery

if TYPE_CHECKING:
    from pathlib import Path


def test_corrupt_worker_checkpoint_does_not_block_next_restore(tmp_path: Path) -> None:
    worker = _registry(tmp_path, FakeWorkerProvider()).start(
        "work", str(tmp_path), provider="fake", harness="test"
    )
    checkpoint = SessionContext().checkpoint()
    good = replace(worker, last_report={**checkpoint})
    bad = replace(
        worker, id="bad", last_report={"last_assistant_message": "unsequenced"}
    )
    store = Mock(spec=HookContextStore)
    WorkerRecovery.restore([bad, good], store)
    store.restore.assert_called_once_with(good.id, checkpoint)
