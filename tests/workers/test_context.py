from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import replace
from typing import TYPE_CHECKING
from unittest.mock import patch

from starlette.testclient import TestClient
from test_workers import FakeWorkerProvider, TrivialProfile, _registry

from punt_vox.voxd.daemon import build_app
from punt_vox.voxd.hook_context_store import HookContextStore
from punt_vox.voxd.workers.actor import WorkerActor
from punt_vox.voxd.workers.reaper import WorkerReaper
from punt_vox.voxd.workers.records import WorkerRecords
from punt_vox.voxd.workers.tmux import TmuxWorkerProvider
from punt_vox.voxd.workers.wiring import WorkerSubsystem

if TYPE_CHECKING:
    from pathlib import Path

    import pytest
    from starlette.applications import Starlette


def test_worker_records_load_once_during_recovery() -> None:
    with (
        patch.object(WorkerRecords, "load", return_value=[]) as load,
        patch.object(WorkerReaper, "run"),
    ):
        WorkerSubsystem.recover(HookContextStore(), WorkerSubsystem.policy())
        load.assert_called_once_with(strict=False)


def test_daemon_shutdown_cancels_queued_worker_cleanup(tmp_path: Path) -> None:
    provider = FakeWorkerProvider()
    registry = _registry(tmp_path, provider)
    one, two = (
        registry.start(task, str(tmp_path), provider="fake", harness="test")
        for task in ("one", "two")
    )
    provider.alive = False
    started, release, queued = (threading.Event() for _ in range(3))

    def cleanup(path: Path) -> None:
        if str(path) == one.worktree:
            started.set()
            assert release.wait(3)
        if str(path) == two.worktree:
            queued.set()

    with (
        patch.object(TrivialProfile, "cleanup", side_effect=cleanup),
        patch.object(WorkerSubsystem, "recover", return_value=WorkerActor(registry)),
    ):
        try:
            with TestClient(build_app()):
                registry.list()
                assert started.wait(1)
                before_shutdown = time.monotonic()
            assert time.monotonic() - before_shutdown < 1.0
        finally:
            release.set()
        assert not queued.wait(0.5)
        saved = WorkerRecords(tmp_path / "workers").load()
        assert next(r for r in saved if r.id == two.id).last_report["cleanup_pending"]


def _rpc_health(app: Starlette) -> None:
    with TestClient(app).websocket_connect("/ws") as websocket:
        websocket.send_json({"type": "health", "id": "health"})
        response = websocket.receive_json()
    assert response["type"] == response["id"] == "health"


def test_worker_recovery_failure_keeps_health_playback_and_ordinary_hooks(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with patch.object(WorkerRecords, "load", side_effect=OSError("disk unavailable")):
        app = build_app()
    _rpc_health(app)
    with TestClient(app).websocket_connect("/ws") as websocket:
        for request, expected in (
            ({"type": "program_status"}, "program_status"),
            (
                {
                    "type": "hook_event",
                    "payload": {
                        "session_id": "ordinary",
                        "hook_event_name": "SessionStart",
                        "relay_seq": 1,
                    },
                },
                "hook_event_accepted",
            ),
            ({"type": "worker_list"}, "error"),
            ({"type": "worker_hook_admission"}, "error"),
        ):
            websocket.send_json({"id": "test", **request})
            response = websocket.receive_json()
            assert response["type"] == expected
            if expected == "error":
                assert "Worker startup recovery failed" in response["message"]
    assert "Worker startup recovery failed" in caplog.text


async def test_daemon_starts_within_budget_despite_multiple_stalled_probes(
    tmp_path: Path,
) -> None:
    registry = _registry(tmp_path, FakeWorkerProvider())
    records = WorkerRecords(tmp_path / "workers")
    for task in ("one", "two"):
        worker = registry.start(task, str(tmp_path), provider="fake", harness="test")
        records.save(replace(worker, provider="tmux"))
    simultaneous = threading.Barrier(2)
    release = threading.Event()

    def stalled(_handle: str) -> bool:
        simultaneous.wait(timeout=3)
        assert release.wait(5)
        return False

    with (
        patch("punt_vox.voxd.workers.wiring.user_state_dir", return_value=tmp_path),
        patch.object(TmuxWorkerProvider, "status", side_effect=stalled) as status,
        patch.object(TmuxWorkerProvider, "stop") as stop,
    ):
        try:
            async with asyncio.timeout(2.5):
                app = await asyncio.to_thread(build_app)
                assert status.call_count == 2
                await asyncio.to_thread(_rpc_health, app)
        finally:
            release.set()
        assert all(record.state != "exited" for record in records.load())
        stop.assert_not_called()
