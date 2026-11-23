from __future__ import annotations

import asyncio
import threading
from dataclasses import replace
from typing import TYPE_CHECKING
from unittest.mock import patch

from starlette.testclient import TestClient
from test_workers import FakeWorkerProvider, _registry

from punt_vox.voxd.daemon import build_app
from punt_vox.voxd.hook_context_store import HookContextStore
from punt_vox.voxd.workers.reaper import WorkerReaper
from punt_vox.voxd.workers.records import WorkerRecords
from punt_vox.voxd.workers.tmux import TmuxWorkerProvider
from punt_vox.voxd.workers.wiring import WorkerSubsystem

if TYPE_CHECKING:
    from pathlib import Path

    from starlette.applications import Starlette


def test_worker_records_load_once_during_recovery() -> None:
    with (
        patch.object(WorkerRecords, "load", return_value=[]) as load,
        patch.object(WorkerReaper, "run"),
    ):
        WorkerSubsystem.recover(HookContextStore(), WorkerSubsystem.policy())
        load.assert_called_once_with(strict=False)


def _rpc_health(app: Starlette) -> None:
    with TestClient(app).websocket_connect("/ws") as websocket:
        websocket.send_json({"type": "health", "id": "health"})
        response = websocket.receive_json()
    assert response["type"] == response["id"] == "health"


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
        patch.object(TmuxWorkerProvider, "status", side_effect=stalled),
        patch.object(TmuxWorkerProvider, "stop") as stop,
    ):
        try:
            async with asyncio.timeout(2.5):
                app = await asyncio.to_thread(build_app)
                assert not simultaneous.n_waiting
                await asyncio.to_thread(_rpc_health, app)
        finally:
            release.set()
        assert all(record.state != "exited" for record in records.load())
        stop.assert_not_called()
