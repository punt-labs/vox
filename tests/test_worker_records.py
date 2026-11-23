"""Typed record persistence and explicit worker control semantics."""

from __future__ import annotations

import json
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch
from uuid import uuid4

import pytest
from test_workers import FakeWorkerProvider, _registry

from punt_vox.voxd.workers.reaper import WorkerReaper
from punt_vox.voxd.workers.records import WorkerRecords
from punt_vox.voxd.workers.tmux import TmuxWorkerProvider


def test_unknown_worker_controls_are_clear(tmp_path: Path) -> None:
    registry = _registry(tmp_path, FakeWorkerProvider())
    with pytest.raises(ValueError, match=r"^no worker missing$"):
        registry.stop("missing")
    with pytest.raises(ValueError, match=r"^no worker missing$"):
        registry.steer("missing", "hello")


def test_ready_survives_restart_until_provider_exits(tmp_path: Path) -> None:
    provider = FakeWorkerProvider()
    provider.ready = True
    registry = _registry(tmp_path, provider)
    ready = registry.start("edit", str(tmp_path), provider="fake", harness="test")
    assert ready.state == "ready"
    provider.ready = False
    restored = _registry(tmp_path, provider)
    assert restored.list()[0] == ready
    provider.alive = False
    assert restored.list()[0].state == "exited"


def test_startup_only_reaps_marked_orphans(tmp_path: Path) -> None:
    registered = _registry(tmp_path, FakeWorkerProvider()).start(
        "edit", str(tmp_path), provider="fake", harness="test"
    )
    records = WorkerRecords(tmp_path / "workers")
    orphan_id = str(uuid4())
    orphan = f"vox-worker-{orphan_id}"
    foreign_id = str(uuid4())
    foreign = f"vox-worker-{foreign_id}"

    listed = CompletedProcess(
        [],
        0,
        f"personal\nvox-worker-invalid\n{registered.session}\n{foreign}\n{orphan}\n",
    )
    other = CompletedProcess([], 0, f"VOX_WORKER_OWNER=other:{foreign_id}\n")
    owned = CompletedProcess(
        [], 0, f"VOX_WORKER_OWNER={records.namespace}:{orphan_id}\n"
    )
    with (
        patch(
            "punt_vox.voxd.workers.reaper.WorkerCommand.run",
            side_effect=[listed, other, owned],
        ),
        patch.object(TmuxWorkerProvider, "stop") as stop,
    ):
        WorkerReaper.run(records, TmuxWorkerProvider())
    stop.assert_called_once_with(orphan)


def test_startup_runs_other_reaper_when_tmux_is_unavailable(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    records = WorkerRecords(tmp_path / "workers")
    with (
        patch("punt_vox.voxd.workers.reaper.WorkerCommand.run", side_effect=OSError),
        patch.object(
            WorkerReaper, "reap_brokers", side_effect=PermissionError
        ) as brokers,
    ):
        WorkerReaper.run(records, TmuxWorkerProvider())
    brokers.assert_called_once_with(records.namespace)
    assert "Worker session reaper unavailable (OSError)" in caplog.text
    assert "Worker broker reaper unavailable (PermissionError)" in caplog.text


def test_unreadable_worker_record_protects_its_session(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    registry = _registry(tmp_path, FakeWorkerProvider())
    worker = registry.start("work", str(tmp_path), provider="fake", harness="test")
    records = WorkerRecords(tmp_path / "workers")
    path = tmp_path / "workers" / f"{worker.id}.json"
    worker_id = str(uuid4())
    mismatched = path.with_name(f"{worker_id}.json")
    path.rename(mismatched)

    assert records.load(strict=False) == []
    assert records.protects_session(worker_id)
    assert f"Invalid worker record {mismatched.name} (ValueError)" in caplog.text


def test_startup_keeps_reaping_after_one_orphan_cannot_stop(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    records = WorkerRecords(tmp_path / "workers")
    ids = (uuid4(), uuid4())
    handles = [f"vox-worker-{worker_id}" for worker_id in ids]
    listed = CompletedProcess([], 0, "\n".join(handles))
    owners = [
        CompletedProcess([], 0, f"VOX_WORKER_OWNER={records.namespace}:{worker_id}")
        for worker_id in ids
    ]
    with (
        patch(
            "punt_vox.voxd.workers.reaper.WorkerCommand.run",
            side_effect=[listed, *owners],
        ),
        patch.object(
            TmuxWorkerProvider, "stop", side_effect=[RuntimeError, None]
        ) as stop,
    ):
        WorkerReaper.run(records, TmuxWorkerProvider())
    assert [call.args[0] for call in stop.call_args_list] == handles
    assert f"Worker orphan {handles[0]} not reaped (RuntimeError)" in caplog.text


def test_ready_worker_accepts_fresh_post_restart_report(tmp_path: Path) -> None:
    provider = FakeWorkerProvider()
    provider.ready = True
    registry = _registry(tmp_path, provider)
    ready = registry.start("edit", str(tmp_path), provider="fake", harness="test")
    provider.ready = False
    restored = _registry(tmp_path, provider)
    assert restored.list()[0] == ready
    report = {"session_started": False, "last_assistant_message": "new completion"}
    with patch.object(FakeWorkerProvider, "progress", return_value=report):
        updated = restored.list()[0]
    assert updated.state == "ready"
    assert updated.last_report == report


@pytest.mark.parametrize("exited", [False, True])
def test_terminal_stop_is_a_noop(tmp_path: Path, exited: bool) -> None:
    provider = FakeWorkerProvider()
    registry = _registry(tmp_path, provider)
    record = registry.start("edit", str(tmp_path), provider="fake", harness="test")
    if exited:
        provider.alive = False
        record = registry.stop(record.id)
        assert record.state == "exited"
    else:
        record = registry.stop(str(record.id))
    with patch.object(FakeWorkerProvider, "stop") as stop:
        assert registry.stop(str(record.id)) == record
        stop.assert_not_called()


@pytest.mark.parametrize(
    ("field", "value"),
    [("state", "unknown"), ("created_at", 123), ("last_report", [])],
)
def test_disk_edge_rejects_invalid_typed_fields(
    tmp_path: Path, field: str, value: object
) -> None:
    registry = _registry(tmp_path, FakeWorkerProvider())
    record = registry.start("edit", str(tmp_path), provider="fake", harness="test")
    path = tmp_path / "workers" / f"{record.id}.json"
    data = json.loads(path.read_text())
    data[field] = value
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="Invalid worker record"):
        WorkerRecords(tmp_path / "workers").load()
