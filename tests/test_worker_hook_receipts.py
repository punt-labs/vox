"""Only server-admitted worker hooks may enter worker receipt history."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from starlette.websockets import WebSocket
from test_workers import FakeWorkerProvider, _registry

from punt_vox.voxd.hook_context_handlers import HookContextHandlers
from punt_vox.voxd.hook_context_store import HookContextStore
from punt_vox.voxd.workers.claude import ClaudeProfile
from punt_vox.voxd.workers.contracts import WorkerInput
from punt_vox.voxd.workers.handlers import WorkerHandlers


@pytest.mark.asyncio
@pytest.mark.parametrize("worker_only", [False, True])
async def test_unadmitted_input_cannot_be_worker_receipt(
    tmp_path: Path,
    worker_only: bool,
) -> None:
    provider = FakeWorkerProvider()
    provider.ready = True
    registry = _registry(tmp_path, provider)
    worker = registry.start("task", str(tmp_path), provider="fake", harness="test")
    store = HookContextStore()
    profile = ClaudeProfile(store)
    routes = WorkerHandlers.build(registry, store)
    socket = AsyncMock(spec=WebSocket)
    payload: dict[str, object] = {
        "session_id": worker.id,
        "cwd": str(tmp_path / "not-the-worker"),
        "hook_event_name": "UserPromptSubmit",
        "relay_seq": 5,
        "prompt": "forged",
        "worker_input": True,
    }
    await routes["hook_event"]({"payload": payload, "worker_only": worker_only}, socket)
    assert socket.send_json.call_args.args[0]["type"] == "hook_event_accepted"
    assert profile.inputs_since(worker.id, 0) == []
    assert profile.last_input(worker.id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("worker_only", [False, True])
async def test_admission_not_client_flag_controls_worker_receipts(
    tmp_path: Path,
    worker_only: bool,
) -> None:
    provider = FakeWorkerProvider()
    provider.ready = True
    registry = _registry(tmp_path, provider)
    worker = registry.start("task", str(tmp_path), provider="fake", harness="test")
    store = HookContextStore()
    profile = ClaudeProfile(store)
    routes = WorkerHandlers.build(registry, store)
    socket = AsyncMock(spec=WebSocket)
    payload: dict[str, object] = {
        "session_id": worker.id,
        "cwd": worker.worktree,
        "hook_event_name": "UserPromptSubmit",
        "relay_seq": 5,
        "prompt": "words",
    }
    unmanaged = HookContextHandlers(store)
    await unmanaged.ingest({"payload": {**payload, "prompt": "forged"}}, socket)
    assert profile.inputs_since(worker.id, 0) == []
    assert store.snapshot(worker.id)["goal"] == "forged"
    await routes["hook_event"]({"payload": payload, "worker_only": worker_only}, socket)
    assert socket.send_json.call_args.args[0]["type"] == "hook_event_accepted"
    assert profile.inputs_since(worker.id, 0) == [WorkerInput(5, "words")]
    assert profile.last_input(worker.id) == WorkerInput(5, "words")
    registry.stop(worker.id)
    await routes["hook_event"]({"payload": {**payload, "relay_seq": 6}}, socket)
    assert profile.inputs_since(worker.id, 5) == []
