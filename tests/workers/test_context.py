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


def _rpc_health(app: Starlette) -> None:
    with TestClient(app).websocket_connect("/ws") as websocket:
        websocket.send_json({"type": "health", "id": "health"})
        response = websocket.receive_json()
    assert response["type"] == response["id"] == "health"


def test_worker_records_load_once_during_recovery() -> None:
    with (
        patch.object(WorkerRecords, "load", return_value=[]) as load,
        patch.object(WorkerReaper, "run"),
    ):
        _, recover = WorkerSubsystem.prepare(
            HookContextStore(), WorkerSubsystem.policy()
        )
        load.assert_not_called()
        recover()
        load.assert_called_once_with(strict=False)


async def test_daemon_serves_health_rpc_during_stalled_worker_probe(
    tmp_path: Path,
) -> None:
    provider = FakeWorkerProvider()
    worker = _registry(tmp_path, provider).start(
        "work", str(tmp_path), provider="fake", harness="test"
    )
    WorkerRecords(tmp_path / "workers").save(replace(worker, provider="tmux"))
    started = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()

    def stalled(_handle: str) -> bool:
        loop.call_soon_threadsafe(started.set)
        assert release.wait(5)
        return True

    with (
        patch("punt_vox.voxd.workers.wiring.user_state_dir", return_value=tmp_path),
        patch.object(TmuxWorkerProvider, "status", side_effect=stalled),
        patch.object(WorkerReaper, "run"),
    ):
        try:
            async with asyncio.timeout(2):
                app = await asyncio.to_thread(build_app)
                await started.wait()
                await asyncio.to_thread(_rpc_health, app)
        finally:
            release.set()
