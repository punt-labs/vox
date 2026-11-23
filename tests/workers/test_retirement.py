from __future__ import annotations

from dataclasses import replace
from threading import Event
from typing import TYPE_CHECKING
from unittest.mock import Mock

from test_workers import FakeWorkerProvider, TrivialProfile, _registry

from punt_vox.voxd.workers.retirement import WorkerRetirement

if TYPE_CHECKING:
    from pathlib import Path


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
    retirement = WorkerRetirement({"test": profile})
    retirement.schedule(record)
    try:
        assert started.wait(1)
        assert retirement.status(record) == record
    finally:
        release.set()

    late = replace(record, last_report={"cleanup_pending": True, "worker_seq": 8})
    assert retirement.finish(late).last_report == {"worker_seq": 8}
    profile.cleanup.assert_called_once()
