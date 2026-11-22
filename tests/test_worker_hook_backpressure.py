"""A busy worker actor must not lose a managed worker's one-time hook."""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
from test_workers import FakeWorkerProvider, _registry
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from punt_vox.hook_context_client import HookContextClient
from punt_vox.voxd.hook_context_handlers import HookContextHandlers
from punt_vox.voxd.hook_context_store import HookContextStore
from punt_vox.voxd.workers.hooks import WorkerHooks
from punt_vox.voxd.workers.records import WorkerRecords

if TYPE_CHECKING:
    from websockets.asyncio.server import ServerConnection

    from punt_vox.voxd.workers.contracts import HarnessProfile


@pytest.mark.asyncio
@pytest.mark.slow
async def test_hook_queued_during_launch_outlives_its_client_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = HookContextStore()

    def progress(
        _self: FakeWorkerProvider, worker_id: str, _profile: HarnessProfile
    ) -> dict[str, object]:
        try:
            return dict(store.snapshot(worker_id))
        except ValueError:
            return {"session_started": False}

    monkeypatch.setattr(FakeWorkerProvider, "progress", progress)
    registry = _registry(tmp_path, FakeWorkerProvider())
    record = registry.start("edit", str(tmp_path), provider="fake", harness="test")
    lock = asyncio.Lock()
    hooks = WorkerHooks(store, registry, lock)
    handlers = HookContextHandlers(store, hooks.ingest).handlers()

    async def connection(websocket: ServerConnection) -> None:
        async def send(payload: dict[str, object]) -> None:
            await websocket.send(json.dumps(payload))

        response = AsyncMock()
        response.send_json.side_effect = send
        with suppress(ConnectionClosed):
            async for raw in websocket:
                message: dict[str, object] = json.loads(raw)
                if message["type"] == "worker_hook_admission":
                    await hooks.admit(message, response)
                else:
                    await handlers["hook_event"](message, response)

    async with serve(connection, "127.0.0.1", 0) as server:
        address = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"

        async def request(
            message: dict[str, object], *, expected: str
        ) -> dict[str, object]:
            async with connect(address, proxy=None) as websocket:
                await websocket.send(json.dumps(message))
                raw = await asyncio.wait_for(websocket.recv(), timeout=3)
                reply: dict[str, object] = json.loads(raw)
                assert reply["type"] == expected
                return reply

        await lock.acquire()
        try:
            with (
                patch.object(HookContextClient, "_request", side_effect=request),
                suppress(TimeoutError),
            ):
                await HookContextClient.send_event(
                    {
                        "session_id": record.id,
                        "cwd": record.worktree,
                        "hook_event_name": "SessionStart",
                        "relay_seq": 1,
                    },
                    worker_only=True,
                )
        finally:
            lock.release()

    assert WorkerRecords(tmp_path / "workers").load()[0].state == "ready"
