from __future__ import annotations

import asyncio
import threading
from dataclasses import replace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
from starlette.testclient import TestClient
from test_workers import FakeWorkerProvider, _registry

from punt_vox.voxd.daemon import build_app
from punt_vox.voxd.hook_context_store import HookContextStore
from punt_vox.voxd.playback import PlaybackQueue
from punt_vox.voxd.workers.context import WorkerContext
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


async def test_ordinary_hook_and_call_status_bypass_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "punt_vox.voxd.workers.startup_handlers._RECOVERY_TIMEOUT_S", 0.2
    )
    release = threading.Event()

    def stalled(_records: WorkerRecords, _provider: TmuxWorkerProvider) -> None:
        assert release.wait(5)

    with (
        patch("punt_vox.voxd.workers.wiring.user_state_dir", return_value=tmp_path),
        patch.object(WorkerReaper, "run", side_effect=stalled),
    ):
        routes = WorkerContext.handlers(PlaybackQueue())
        socket = AsyncMock()
        hook = {
            "session_id": "ordinary",
            "hook_event_name": "SessionStart",
            "relay_seq": 1,
        }
        managed_socket = AsyncMock()
        managed = asyncio.create_task(
            routes["hook_event"](
                {"type": "hook_event", "worker_only": True, "payload": hook},
                managed_socket,
            )
        )
        try:
            for route, message, expected in (
                ("hook_event", {"payload": hook}, "hook_event_accepted"),
                ("hook_context", {"session_id": "ordinary"}, "hook_context"),
                ("call_status", {}, "call_result"),
            ):
                await routes[route]({"type": route, **message}, socket)
                assert socket.send_json.call_args.args[0]["type"] == expected
            await asyncio.sleep(0)
            managed_socket.send_json.assert_not_awaited()
        finally:
            release.set()
            await managed
        assert (
            managed_socket.send_json.call_args.args[0]["type"] == "hook_event_accepted"
        )


async def test_stop_that_reports_recovery_pending_never_terminates_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "punt_vox.voxd.workers.startup_handlers._RECOVERY_TIMEOUT_S", 0.05
    )
    worker = _registry(tmp_path, FakeWorkerProvider()).start(
        "work", str(tmp_path), provider="fake", harness="test"
    )
    WorkerRecords(tmp_path / "workers").save(replace(worker, provider="tmux"))
    release = threading.Event()

    def stalled(_records: WorkerRecords, _provider: TmuxWorkerProvider) -> None:
        assert release.wait(5)

    with (
        patch("punt_vox.voxd.workers.wiring.user_state_dir", return_value=tmp_path),
        patch.object(TmuxWorkerProvider, "status", return_value=True),
        patch.object(TmuxWorkerProvider, "stop") as stop,
        patch.object(WorkerReaper, "run", side_effect=stalled),
    ):
        routes = WorkerContext.handlers(PlaybackQueue())
        socket = AsyncMock()
        try:
            await routes["worker_stop"](
                {"type": "worker_stop", "worker_id": worker.id}, socket
            )
            assert (
                socket.send_json.call_args.args[0]["code"] == "worker_recovery_pending"
            )
        finally:
            release.set()
        await routes["worker_list"]({"type": "worker_list"}, socket)
        assert socket.send_json.call_args.args[0]["type"] == "worker_result"
        stop.assert_not_called()


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
