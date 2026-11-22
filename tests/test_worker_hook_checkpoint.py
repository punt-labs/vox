"""Delivered managed-worker hooks must survive the next daemon restart."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
from test_workers import FakeWorkerProvider, TrivialProfile, _registry

from punt_vox.hook_context_client import HookContextClient
from punt_vox.hook_context_relay import HookContextRelay, main
from punt_vox.voxd.hook_context_handlers import HookContextHandlers
from punt_vox.voxd.hook_context_store import HookContextStore
from punt_vox.voxd.playback import PlaybackQueue
from punt_vox.voxd.workers.claude import ClaudeProfile
from punt_vox.voxd.workers.context import WorkerContext
from punt_vox.voxd.workers.handlers import WorkerHandlers
from punt_vox.voxd.workers.records import WorkerRecords
from punt_vox.voxd.workers.registry import WorkerRegistry

if TYPE_CHECKING:
    from punt_vox.voxd.workers.contracts import HarnessProfile


def _profile_progress(worker: str, harness: HarnessProfile) -> dict[str, object]:
    return harness.progress(worker)


def test_hook_sender_does_not_open_the_worker_registry(tmp_path: Path) -> None:
    with (
        patch.object(
            WorkerRecords,
            "__new__",
            side_effect=AssertionError("hook sender opened worker records"),
        ),
        patch("punt_vox.hook_context_admission.find_repo_root", return_value=tmp_path),
        patch(
            "punt_vox.hook_context_admission.VoxMarker.is_present", return_value=False
        ),
    ):
        assert not HookContextRelay(
            {"session_id": "unknown", "cwd": str(tmp_path)}
        ).enabled()


@pytest.mark.asyncio
async def test_delivered_session_start_is_durable_without_list(tmp_path: Path) -> None:
    store = HookContextStore()
    provider = FakeWorkerProvider()
    profile = ClaudeProfile(store)
    with (
        patch.object(
            FakeWorkerProvider,
            "progress",
            side_effect=_profile_progress,
        ),
        patch.object(TrivialProfile, "progress", side_effect=profile.progress),
    ):
        registry = _registry(tmp_path, provider)
        record = registry.start("edit", str(tmp_path), provider="fake", harness="test")
        assert record.state == "starting"
        with (
            patch("punt_vox.voxd.workers.context.HookContextStore", return_value=store),
            patch("punt_vox.voxd.workers.wiring.WorkerRegistry", return_value=registry),
            patch("punt_vox.voxd.workers.wiring.user_state_dir", return_value=tmp_path),
        ):
            handlers = WorkerContext.handlers(PlaybackQueue())
        socket = AsyncMock()
        await handlers["hook_event"](
            {
                "id": "hook",
                "payload": {
                    "session_id": record.id,
                    "cwd": record.worktree,
                    "hook_event_name": "SessionStart",
                    "relay_seq": 1,
                    "private": "never-persist-raw-hooks",
                },
            },
            socket,
        )
        assert socket.send_json.call_args.args[0]["type"] == "hook_event_accepted"
        persisted = WorkerRecords(tmp_path / "workers").load()[0]
        assert persisted.state == "ready"
        assert (
            "never-persist-raw-hooks"
            not in (tmp_path / "workers" / f"{record.id}.json").read_text()
        )
    assert _registry(tmp_path, provider).list()[0].state == "ready"


@pytest.mark.asyncio
async def test_unadmitted_worker_events_never_leave_the_client() -> None:
    payload: dict[str, object] = {
        "session_id": "unknown",
        "cwd": "/repo",
        "prompt": "private prompt",
    }
    with patch.object(
        HookContextClient,
        "_request",
        new_callable=AsyncMock,
        return_value={"admitted": False},
    ) as request:
        await HookContextClient.send_event(payload, worker_only=True)
    request.assert_awaited_once_with(
        {"type": "worker_hook_admission", "session_id": "unknown", "cwd": "/repo"},
        expected="worker_hook_admission",
    )


@pytest.mark.asyncio
async def test_admitted_event_retains_server_side_revalidation() -> None:
    payload: dict[str, object] = {"session_id": "worker", "cwd": "/repo"}
    with patch.object(
        HookContextClient,
        "_request",
        new_callable=AsyncMock,
        side_effect=[{"admitted": True}, {}],
    ) as request:
        await HookContextClient.send_event(payload, worker_only=True)
    assert request.await_count == 2
    assert request.call_args.args[0] == {
        "type": "hook_event",
        "payload": payload,
        "worker_only": True,
    }


@pytest.mark.asyncio
async def test_stopping_between_admission_and_delivery_discards_the_hook(
    tmp_path: Path,
) -> None:
    store = HookContextStore()
    registry = _registry(tmp_path, FakeWorkerProvider())
    record = registry.start("edit", str(tmp_path), provider="fake", harness="test")
    routes = WorkerHandlers.build(registry, store)
    socket = AsyncMock()
    await routes["worker_hook_admission"](
        {"session_id": record.id, "cwd": record.worktree}, socket
    )
    assert socket.send_json.call_args.args[0]["admitted"] is True
    registry.stop(record.id)
    await routes["hook_event"](
        {
            "worker_only": True,
            "payload": {
                "session_id": record.id,
                "cwd": record.worktree,
                "hook_event_name": "SessionStart",
                "relay_seq": 1,
            },
        },
        socket,
    )
    with pytest.raises(ValueError, match="no hook context"):
        store.snapshot(record.id)
    assert WorkerRecords(tmp_path / "workers").load()[0].state == "stopped"


@pytest.mark.asyncio
async def test_failed_checkpoint_is_not_acknowledged(tmp_path: Path) -> None:
    store = HookContextStore()
    provider = FakeWorkerProvider()
    registry = _registry(tmp_path, provider)
    record = registry.start("edit", str(tmp_path), provider="fake", harness="test")
    provider.ready = True
    routes = WorkerHandlers.build(registry, store)
    socket = AsyncMock()
    with patch.object(WorkerRecords, "save", side_effect=OSError("disk failed")):
        await routes["hook_event"](
            {
                "payload": {
                    "session_id": record.id,
                    "cwd": record.worktree,
                    "hook_event_name": "SessionStart",
                    "relay_seq": 1,
                }
            },
            socket,
        )
    assert socket.send_json.call_args.args[0]["type"] == "error"
    assert WorkerRecords(tmp_path / "workers").load()[0].state == "starting"


@pytest.mark.parametrize("contents", ["[]", '{"_vox_worker_only": "yes"}'])
def test_invalid_staged_admission_metadata_is_rejected(contents: str) -> None:
    with (
        patch("sys.stdin", io.StringIO(contents)),
        patch("punt_vox.hook_context_relay.configure_client_logging"),
    ):
        assert main() == 1


def test_detached_delivery_preserves_admission_metadata() -> None:
    payload = {"session_id": "worker", "cwd": "/repo", "_vox_worker_only": True}
    with (
        patch("sys.stdin", io.StringIO(json.dumps(payload))),
        patch("punt_vox.hook_context_relay.configure_client_logging"),
        patch.object(HookContextClient, "send_event", new_callable=AsyncMock) as send,
    ):
        assert main() == 0
    send.assert_awaited_once_with(
        {"session_id": "worker", "cwd": "/repo"}, worker_only=True
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("worker_only", [False, True, "not a boolean"])
async def test_standalone_hook_ingestion_validates_admission_flags(
    worker_only: object,
) -> None:
    socket = AsyncMock()
    await HookContextHandlers().ingest(
        {
            "worker_only": worker_only,
            "payload": {
                "session_id": "session",
                "hook_event_name": "SessionStart",
                "relay_seq": 1,
            },
        },
        socket,
    )
    expected = "hook_event_accepted" if worker_only is False else "error"
    assert socket.send_json.call_args.args[0]["type"] == expected


@pytest.mark.asyncio
async def test_recursive_envelopes_have_a_safe_ingestion_error() -> None:
    store = HookContextStore()
    socket = AsyncMock()
    with patch.object(HookContextStore, "ingest", side_effect=RecursionError):
        await HookContextHandlers(store).ingest({"payload": {}}, socket)
    assert socket.send_json.call_args.args[0]["type"] == "error"
    assert "nesting exceeds" in str(socket.send_json.call_args)


@pytest.mark.asyncio
async def test_admission_path_errors_are_wire_errors(tmp_path: Path) -> None:
    registry = _registry(tmp_path, FakeWorkerProvider())
    socket = AsyncMock()
    with patch.object(
        WorkerRegistry, "admits_hook", side_effect=OSError("path unavailable")
    ):
        await WorkerHandlers.build(registry, HookContextStore())[
            "worker_hook_admission"
        ]({}, socket)
    assert socket.send_json.call_args.args[0]["type"] == "error"


@pytest.mark.asyncio
async def test_unknown_worker_commands_are_wire_errors(tmp_path: Path) -> None:
    registry = _registry(tmp_path, FakeWorkerProvider())
    socket = AsyncMock()
    await WorkerHandlers(registry).handle({"type": "unknown"}, socket)
    assert socket.send_json.call_args.args[0]["type"] == "error"


def test_missing_repository_identity_cannot_opt_in() -> None:
    assert not HookContextRelay({}).enabled()


def test_claude_profile_preparation_and_cleanup_use_the_native_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    profile = ClaudeProfile(HookContextStore())
    worktree = tmp_path / "worktree"
    profile.prepare(worktree)
    path = tmp_path / ".claude.json"
    assert (
        json.loads(path.read_text())["projects"][str(worktree)][
            "hasTrustDialogAccepted"
        ]
        is True
    )
    profile.cleanup(worktree)
    assert str(worktree) not in json.loads(path.read_text())["projects"]


def test_registry_steering_uses_its_selected_provider(tmp_path: Path) -> None:
    provider = FakeWorkerProvider()
    provider.ready = True
    registry = _registry(tmp_path, provider)
    record = registry.start("edit", str(tmp_path), provider="fake", harness="test")
    with patch.object(FakeWorkerProvider, "steer") as steer:
        registry.steer(record.id, "continue", urgent=True)
    assert steer.call_args.args == (record.session, "continue")
    assert steer.call_args.kwargs["urgent"] is True
