"""Worker policy, persistence, profiles and the call-side RPC boundary."""

from __future__ import annotations

import json
import stat
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from starlette.websockets import WebSocket

from punt_vox.client_errors import VoxdProtocolError
from punt_vox.config import ConfigStore
from punt_vox.process_env import ProcessEnv
from punt_vox.voxd.hook_context_store import HookContextStore
from punt_vox.voxd.workers.claude import ClaudeProfile
from punt_vox.voxd.workers.command import WorkerCommand
from punt_vox.voxd.workers.contracts import HarnessProfile, WorkerLaunch
from punt_vox.voxd.workers.handlers import WorkerHandlers
from punt_vox.voxd.workers.policy import WorkerRepoPolicy
from punt_vox.voxd.workers.records import WorkerRecords
from punt_vox.voxd.workers.registry import WorkerRegistry
from punt_vox.voxd.workers.tmux import TmuxWorkerProvider
from punt_vox.voxd.workers.trust import ClaudeTrust
from punt_vox.worker_client import WorkerClient


class FakeWorkerProvider:
    """A live provider with explicitly controlled readiness."""

    alive = False
    ready = False
    fail = False

    def start(
        self,
        launch: WorkerLaunch,
        profile: HarnessProfile,
    ) -> None:
        if self.fail:
            raise OSError("launch failed")
        self.alive = True

    def steer(
        self, handle: str, words: str, *, urgent: bool, profile: HarnessProfile
    ) -> None:
        assert self.alive

    def status(self, handle: str) -> bool:
        return self.alive

    def stop(self, handle: str) -> None:
        self.alive = False

    def progress(self, worker_id: str, profile: HarnessProfile) -> dict[str, object]:
        return {"session_started": self.ready}


class TrivialProfile:
    """A portable line reader used by real tmux tests too."""

    submit_key = "Enter"
    interrupt_key = "Escape"

    def argv(self, worker_id: str, task: str) -> list[str]:
        return [
            "/bin/sh",
            "-c",
            (
                "env > environment; while IFS= read -r line; "
                'do printf "%s\\n" "$line" >> received; done'
            ),
        ]

    def environment(self, parent: Mapping[str, str]) -> dict[str, str]:
        return {k: v for k, v in parent.items() if ProcessEnv.admits(k)}

    def prepare(self, worktree: Path) -> None:
        pass

    def cleanup(self, worktree: Path) -> None:
        pass

    def progress(self, worker_id: str) -> dict[str, object]:
        return {"session_started": True}


def _registry(tmp_path: Path, provider: FakeWorkerProvider) -> WorkerRegistry:
    policy = MagicMock(spec=WorkerRepoPolicy)
    policy.resolve.return_value = tmp_path
    return WorkerRegistry(
        policy,
        WorkerRecords(tmp_path / "workers"),
        {"fake": provider},
        {"test": TrivialProfile()},
    )


@pytest.mark.asyncio
async def test_call_side_start_ready_list_stop(tmp_path: Path) -> None:
    """Boss-facing handlers own the full lifecycle, including empty listing."""
    provider = FakeWorkerProvider()
    registry = _registry(tmp_path, provider)
    handlers = WorkerHandlers.build(registry)
    ws = AsyncMock(spec=WebSocket)

    async def rpc(operation: str, **fields: object) -> object:
        await handlers[f"worker_{operation}"](
            {"type": f"worker_{operation}", "id": "rpc", **fields}, ws
        )
        return ws.send_json.call_args.args[0]["result"]

    assert await rpc("list") == []
    started = cast(
        "dict[str, object]",
        await rpc(
            "start", task="edit", project=str(tmp_path), provider="fake", harness="test"
        ),
    )
    assert started["state"] == "starting"
    provider.ready = True
    listed = cast("list[dict[str, object]]", await rpc("list"))
    assert listed[0]["state"] == "ready"
    stopped = cast("dict[str, object]", await rpc("stop", worker_id=started["id"]))
    assert stopped["state"] == "stopped"
    assert stopped["worktree"] == started["worktree"]
    assert not provider.alive


def test_restart_reconciles_dead_and_preserves_private_records(tmp_path: Path) -> None:
    provider = FakeWorkerProvider()
    registry = _registry(tmp_path, provider)
    record = registry.start("edit", str(tmp_path), provider="fake", harness="test")
    provider.alive = False
    restored = _registry(tmp_path, provider)
    assert restored.list()[0].state == "exited"
    root = tmp_path / "workers"
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    path = root / f"{record.id}.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text())["task"] == "edit"


def test_empty_task_unknown_provider_and_failed_start(tmp_path: Path) -> None:
    provider = FakeWorkerProvider()
    registry = _registry(tmp_path, provider)
    with pytest.raises(ValueError, match="empty"):
        registry.start("", str(tmp_path))
    with pytest.raises(ValueError, match="Unknown"):
        registry.start("task", str(tmp_path), provider="missing")
    provider.fail = True
    with pytest.raises(OSError, match="launch failed"):
        registry.start("task", str(tmp_path), provider="fake", harness="test")
    assert registry.list()[0].state == "stopped"


@pytest.mark.asyncio
async def test_call_side_refuses_unallowlisted_project(tmp_path: Path) -> None:
    registry = WorkerRegistry(
        WorkerRepoPolicy(ConfigStore(tmp_path / "config")),
        WorkerRecords(tmp_path / "workers"),
        {"fake": FakeWorkerProvider()},
        {"test": TrivialProfile()},
    )
    ws = AsyncMock(spec=WebSocket)
    await WorkerHandlers(registry).handle(
        {
            "id": "bad",
            "type": "worker_start",
            "task": "edit",
            "project": str(tmp_path),
            "provider": "fake",
            "harness": "test",
        },
        ws,
    )
    reply = ws.send_json.call_args.args[0]
    assert reply["type"] == "error"
    assert "not allowlisted" in reply["message"]
    assert registry.list() == []


def test_profile_argv_env_and_hook_readiness() -> None:
    store = HookContextStore()
    profile = ClaudeProfile(store)
    worker_id = str(uuid4())
    assert profile.argv(worker_id, "do work") == [
        "claude",
        "--session-id",
        worker_id,
        "--",
        "do work",
    ]
    env = {
        "HOME": "/home/test",
        "LC_ALL": "C",
        "CLAUDE_CONFIG_DIR": "/custom",
        "CLAUDE_CODE_OAUTH_TOKEN": "example",
        "GITHUB_TOKEN": "deny",
        "PI_MODEL": "deny",
    }
    assert profile.environment(env) == {k: v for k, v in env.items() if v != "deny"}
    assert profile.environment({}) == {}
    assert profile.progress(worker_id) == {"session_started": False}
    store.ingest(
        {"session_id": worker_id, "hook_event_name": "SessionStart", "relay_seq": 1}
    )
    assert profile.progress(worker_id)["session_started"] is True


def test_trust_exact_path_preserves_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    path = tmp_path / ".claude.json"
    path.write_text(
        json.dumps(
            {"theme": "dark", "projects": {"/other": {"hasTrustDialogAccepted": True}}}
        )
    )
    worktree = tmp_path / "tree"
    trust = ClaudeTrust()
    trust.update(worktree, accepted=True)
    assert json.loads(path.read_text())["projects"][str(worktree)][
        "hasTrustDialogAccepted"
    ]
    trust.update(worktree, accepted=False)
    assert json.loads(path.read_text()) == {
        "theme": "dark",
        "projects": {"/other": {"hasTrustDialogAccepted": True}},
    }
    path.write_text("[]")
    with pytest.raises(ValueError, match="JSON object"):
        trust.update(worktree, accepted=True)


def test_tmux_urgent_keys_and_safe_launch_error(tmp_path: Path) -> None:
    """Interrupt before literal submission, and never echo launch credentials."""
    provider = TmuxWorkerProvider()
    with patch.object(TmuxWorkerProvider, "_run") as run:
        provider.steer("worker", "words", urgent=True, profile=TrivialProfile())
    assert [c.args for c in run.call_args_list] == [
        ("send-keys", "-t", "=worker:", "Escape"),
        ("send-keys", "-t", "=worker:", "-l", "--", "words"),
        ("send-keys", "-t", "=worker:", "Enter"),
    ]
    with (
        patch.object(WorkerCommand, "_scoped", return_value=False),
        patch(
            "punt_vox.voxd.workers.tmux.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, ["secret-value"]),
        ),
        pytest.raises(RuntimeError, match=r"^Worker tmux operation failed$"),
    ):
        provider.start(
            WorkerLaunch("worker", str(uuid4()), tmp_path, "task"), TrivialProfile()
        )


@pytest.mark.asyncio
async def test_worker_client_roundtrip_and_error() -> None:
    """Check response identity and surface daemon refusals to thin clients."""
    transport = AsyncMock()

    async def reply(request: dict[str, object], **kwargs: object) -> dict[str, object]:
        return {"type": "worker_result", "id": request["id"], "result": []}

    transport.send_and_recv.side_effect = reply
    with patch("punt_vox.worker_client.VoxdTransport", return_value=transport):
        assert await WorkerClient.request("list") == []
        transport.close.assert_awaited_once()
        transport.send_and_recv.side_effect = None
        transport.send_and_recv.return_value = {
            "type": "error",
            "message": "not allowlisted",
        }
        with pytest.raises(VoxdProtocolError, match="not allowlisted"):
            await WorkerClient.request("start", project="/bad", task="edit")


def test_record_rejects_invalid_identity(tmp_path: Path) -> None:
    """Corrupt records fail loudly rather than dropping tracked workers."""
    records = WorkerRecords(tmp_path)
    (tmp_path / f"{uuid4()}.json").write_text("[]")
    with pytest.raises(ValueError, match="Invalid worker record"):
        records.load()
