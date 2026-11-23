from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

from test_workers import FakeWorkerProvider, TrivialProfile, _registry

from punt_vox.voxd.workers.cleanup import WorkerCleanup

if TYPE_CHECKING:
    from pathlib import Path

    from punt_vox.voxd.workers.contracts import HarnessProfile


def test_failed_cleanup_keeps_result_and_is_retryable(tmp_path: Path) -> None:
    registry = _registry(tmp_path, FakeWorkerProvider())
    record = registry.start("work", str(tmp_path), provider="fake", harness="test")
    profiles: dict[str, HarnessProfile] = {"test": TrivialProfile()}
    retired = record.retire()

    with patch.object(TrivialProfile, "cleanup", side_effect=OSError("busy")):
        failed = WorkerCleanup.run(retired, profiles)
    assert WorkerCleanup.pending(failed)
    assert failed.last_report == {**retired.last_report, "cleanup_error": "OSError"}

    cleaned = WorkerCleanup.run(failed, profiles)
    assert cleaned.state == "exited"
    assert cleaned.last_report == record.last_report
    assert not WorkerCleanup.pending(cleaned)
