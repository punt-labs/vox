"""Lifecycle failures must stay isolated and cleanup must remain retryable."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from test_workers import FakeWorkerProvider, TrivialProfile, _registry

from punt_vox.voxd.playback import PlaybackQueue
from punt_vox.voxd.workers.context import WorkerContext
from punt_vox.voxd.workers.records import WorkerRecords


def test_corrupt_worker_record_does_not_block_healthy_workers(tmp_path: Path) -> None:
    provider = FakeWorkerProvider()
    registry = _registry(tmp_path, provider)
    healthy = registry.start("edit", str(tmp_path), provider="fake", harness="test")
    bad = tmp_path / "workers" / f"{uuid4()}.json"
    bad.write_text("broken JSON")
    restored = _registry(tmp_path, provider)
    assert restored.list() == [healthy]
    assert bad.read_text() == "broken JSON"
    with patch("punt_vox.voxd.workers.wiring.user_state_dir", return_value=tmp_path):
        routes = WorkerContext.handlers(PlaybackQueue())
    assert "worker_list" in routes
    assert "hook_event" in routes


@pytest.mark.parametrize("field", ["provider", "harness"])
def test_unknown_catalog_entry_cannot_prevent_rebuild(
    tmp_path: Path, field: str
) -> None:
    provider = FakeWorkerProvider()
    registry = _registry(tmp_path, provider)
    record = registry.start("edit", str(tmp_path), provider="fake", harness="test")
    stale = (
        replace(record, provider="no-longer-installed")
        if field == "provider"
        else replace(record, harness="no-longer-installed")
    )
    WorkerRecords(tmp_path / "workers").save(stale)
    assert _registry(tmp_path, provider).list() == [stale]


def test_failed_start_preserves_original_error_and_cleans_trust(tmp_path: Path) -> None:
    provider = FakeWorkerProvider()
    registry = _registry(tmp_path, provider)
    with (
        patch.object(FakeWorkerProvider, "start", side_effect=OSError("launch failed")),
        patch.object(
            FakeWorkerProvider, "stop", side_effect=RuntimeError("stop failed")
        ),
        patch.object(TrivialProfile, "cleanup") as cleanup,
        pytest.raises(OSError, match="launch failed"),
    ):
        registry.start("edit", str(tmp_path), provider="fake", harness="test")
    cleanup.assert_called_once()
    payload = json.loads(next((tmp_path / "workers").glob("*.json")).read_text())
    assert payload["last_report"]["launch_error"] == "OSError"
    assert payload["last_report"]["stop_error"] == "RuntimeError"
    retried = registry.stop(registry.list()[0].id)
    assert retried.state == "stopped"
    assert "stop_error" not in retried.last_report


def test_rollback_checkpoint_failure_cannot_mask_launch_error(tmp_path: Path) -> None:
    provider = FakeWorkerProvider()
    provider.fail = True
    registry = _registry(tmp_path, provider)
    with (
        patch.object(WorkerRecords, "save", side_effect=[None, OSError("disk failed")]),
        pytest.raises(OSError, match="launch failed"),
    ):
        registry.start("edit", str(tmp_path), provider="fake", harness="test")
    assert registry.list()[0].state == "stopped"


def test_terminal_stop_retries_only_failed_cleanup(tmp_path: Path) -> None:
    provider = FakeWorkerProvider()
    registry = _registry(tmp_path, provider)
    record = registry.start("edit", str(tmp_path), provider="fake", harness="test")
    with patch.object(TrivialProfile, "cleanup", side_effect=TimeoutError("locked")):
        assert registry.stop(record.id).last_report["cleanup_error"] == "TimeoutError"
    with (
        patch.object(FakeWorkerProvider, "stop") as stop,
        patch.object(TrivialProfile, "cleanup") as cleanup,
    ):
        cleaned = registry.stop(record.id)
    cleanup.assert_called_once()
    stop.assert_not_called()
    assert cleaned.state == "stopped"
    assert "cleanup_error" not in cleaned.last_report


def test_dead_worker_is_terminated_before_becoming_terminal(tmp_path: Path) -> None:
    provider = FakeWorkerProvider()
    registry = _registry(tmp_path, provider)
    record = registry.start("edit", str(tmp_path), provider="fake", harness="test")
    provider.alive = False
    with (
        patch.object(FakeWorkerProvider, "stop") as stop,
        patch.object(WorkerRecords, "save") as save,
    ):
        assert registry.list()[0].state == "exited"
        stop.assert_not_called()
        save.assert_not_called()
        assert registry.stop(record.id).state == "exited"
    stop.assert_called_once()
