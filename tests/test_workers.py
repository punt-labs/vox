"""Worker policy, persistence, profiles and the call-side RPC boundary."""

from __future__ import annotations

import errno
import json
import logging
import os
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
from punt_vox.voxd.workers.actor import WorkerActor
from punt_vox.voxd.workers.allowlist_handlers import AllowlistHandlers
from punt_vox.voxd.workers.claude import ClaudeProfile
from punt_vox.voxd.workers.command import WorkerCommand
from punt_vox.voxd.workers.contracts import HarnessProfile, WorkerInput, WorkerLaunch
from punt_vox.voxd.workers.handlers import WorkerHandlers
from punt_vox.voxd.workers.policy import WorkerRepoPolicy
from punt_vox.voxd.workers.records import WorkerRecords
from punt_vox.voxd.workers.registry import WorkerRegistry
from punt_vox.voxd.workers.steering_types import DecisionChoice
from punt_vox.voxd.workers.tmux import TmuxWorkerProvider
from punt_vox.voxd.workers.trust import ClaudeTrust
from punt_vox.worker_client import WorkerClient


class FakeWorkerProvider:
    """A live provider with explicitly controlled readiness."""

    alive = False
    ready = False
    fail = False

    def decide(
        self, handle: str, choice: DecisionChoice, *, profile: HarnessProfile
    ) -> None:
        pass

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
    settle_delay = 0.05
    input_limit = 1000

    def last_input(self, worker_id: str) -> None:
        return None

    def inputs_since(self, worker_id: str, baseline: int) -> list[WorkerInput]:
        return []

    def matches_input(self, receipt: object, words: str) -> bool:
        return False

    def decision_keys(self, choice: DecisionChoice) -> tuple[str, ...]:
        return ("1",) if choice == "approve" else ("Escape",)

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
    records = WorkerRecords(tmp_path / "workers")
    registry = WorkerRegistry(
        policy,
        records,
        {"fake": provider},
        {"test": TrivialProfile()},
    )
    registry.restore(records.load(strict=False))
    return registry


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
    await WorkerHandlers(WorkerActor(registry)).handle(
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
    with (
        patch.object(TmuxWorkerProvider, "_run") as run,
        patch("punt_vox.voxd.workers.tmux.uuid4", return_value="input"),
    ):
        provider.steer("worker", "words", urgent=True, profile=TrivialProfile())
    assert [c.args for c in run.call_args_list] == [
        ("send-keys", "-t", "=worker:", "Escape"),
        ("load-buffer", "-b", "vox-steer-input", "-"),
        ("paste-buffer", "-b", "vox-steer-input", "-t", "=worker:"),
        ("delete-buffer", "-b", "vox-steer-input"),
        ("send-keys", "-t", "=worker:", "Enter"),
    ]
    assert run.call_args_list[1].kwargs == {"input_text": "words"}
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
    with patch("punt_vox.daemon_control.VoxdTransport", return_value=transport):
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


@pytest.mark.asyncio
async def test_call_side_allow_repos_disallow_roundtrip(tmp_path: Path) -> None:
    """The daemon owns the allowlist: allow, list and disallow are worker RPCs."""
    repo = tmp_path / "repo"
    repo.mkdir()
    config = ConfigStore(tmp_path / "config")
    handlers = AllowlistHandlers(WorkerRepoPolicy(config)).handlers()
    ws = AsyncMock(spec=WebSocket)

    async def rpc(operation: str, **fields: object) -> dict[str, object]:
        await handlers[f"worker_{operation}"](
            {"type": f"worker_{operation}", "id": "rpc", **fields}, ws
        )
        return cast("dict[str, object]", ws.send_json.call_args.args[0])

    assert await rpc("repos") == {"type": "worker_result", "id": "rpc", "result": []}
    with patch(
        "punt_vox.voxd.workers.policy.subprocess.run",
        return_value=MagicMock(stdout=f"{repo.resolve()}\n"),
    ):
        allowed = await rpc("allow", project=str(repo))
    assert allowed["result"] == [str(repo.resolve())]
    assert config.read_field("worker_repos") == repr([str(repo.resolve())])
    assert (await rpc("repos"))["result"] == [str(repo.resolve())]
    refused = await rpc("allow", project=str(tmp_path / "missing"))
    assert refused["type"] == "error"
    assert "not a directory" in str(refused["message"])
    for blank in ({}, {"project": ""}, {"project": None}):
        bare = await rpc("allow", **blank)
        assert bare["type"] == "error", blank
        assert "project is required" in str(bare["message"])
    assert config.read_field("worker_repos") == repr([str(repo.resolve())])
    assert (await rpc("disallow", project=str(repo)))["result"] == []
    assert config.read_field("worker_repos") == "[]"


@pytest.mark.asyncio
async def test_allowlist_handler_faults_hide_the_path_and_decode_errors(
    tmp_path: Path,
) -> None:
    """M4: an OSError or a bad encoding is a server fault, never a client rejection."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "vox.md").write_bytes(b'---\nworker_repos: "[]"\n\xff\n---\n')
    handlers = AllowlistHandlers(WorkerRepoPolicy(ConfigStore(config_dir))).handlers()
    ws = AsyncMock(spec=WebSocket)
    await handlers["worker_repos"]({"type": "worker_repos", "id": "r"}, ws)
    reply = ws.send_json.call_args.args[0]
    assert reply["type"] == "error"
    assert str(tmp_path) not in str(reply["message"])
    with patch.object(
        WorkerRepoPolicy,
        "allow",
        side_effect=PermissionError(13, "Permission denied", str(config_dir / "x")),
    ):
        await handlers["worker_allow"](
            {"type": "worker_allow", "id": "a", "project": str(tmp_path)}, ws
        )
    reply = ws.send_json.call_args.args[0]
    assert reply["type"] == "error"
    assert str(tmp_path) not in str(reply["message"])


def _unfenced(path: Path) -> OSError:
    """Return the fault Frontmatter raises for a file with no closing fence."""
    return OSError(errno.EBADMSG, "no closing ---", str(path))


def _broken_config(tmp_path: Path, content: bytes) -> Path:
    """Write *content* as the global vox.md and return the config dir."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "vox.md").write_bytes(content)
    return config_dir


def _assert_path_free_fault(
    reply: dict[str, object], tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The frame is a fault naming no segment of *tmp_path*; the log names it."""
    assert reply["type"] == "error"
    message = str(reply["message"])
    for segment in tmp_path.parts[1:]:
        assert segment not in message, (segment, message)
    faults = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert faults, "a config fault must be audited as a fault"
    assert any("operation failed" in r.getMessage() for r in faults)
    assert not any("rejected op" in r.getMessage() for r in caplog.records)
    # The log keeps the path (its tail may be cut by the log field cap).
    assert any(tmp_path.parts[1] in r.getMessage() for r in faults)


_UNREADABLE = pytest.mark.skipif(os.geteuid() == 0, reason="root reads mode 000")


def _start_request(project: Path) -> dict[str, object]:
    return {
        "id": "s",
        "type": "worker_start",
        "task": "edit",
        "project": str(project),
        "provider": "fake",
        "harness": "test",
    }


def _worker_handlers(config_dir: Path, tmp_path: Path) -> WorkerHandlers:
    registry = WorkerRegistry(
        WorkerRepoPolicy(ConfigStore(config_dir)),
        WorkerRecords(tmp_path / "workers"),
        {"fake": FakeWorkerProvider()},
        {"test": TrivialProfile()},
    )
    return WorkerHandlers(WorkerActor(registry))


@_UNREADABLE
@pytest.mark.asyncio
async def test_worker_start_faults_without_the_path_on_an_unreadable_config(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    config_dir = _broken_config(tmp_path, b'---\nworker_repos: "[]"\n---\n')
    (config_dir / "vox.md").chmod(0)
    ws = AsyncMock(spec=WebSocket)
    try:
        with caplog.at_level(logging.WARNING, logger="punt_vox.voxd.wire_reply"):
            await _worker_handlers(config_dir, tmp_path).handle(
                _start_request(tmp_path), ws
            )
    finally:
        (config_dir / "vox.md").chmod(0o600)
    _assert_path_free_fault(ws.send_json.call_args.args[0], tmp_path, caplog)


@pytest.mark.asyncio
async def test_worker_start_faults_without_the_path_on_a_non_utf8_config(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    config_dir = _broken_config(tmp_path, b'---\nworker_repos: "[]"\n\xff\n---\n')
    ws = AsyncMock(spec=WebSocket)
    with caplog.at_level(logging.WARNING, logger="punt_vox.voxd.wire_reply"):
        await _worker_handlers(config_dir, tmp_path).handle(
            _start_request(tmp_path), ws
        )
    reply = ws.send_json.call_args.args[0]
    assert reply["type"] == "error"
    assert reply["message"] == "operation failed"
    faults = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert any("utf-8" in r.getMessage() for r in faults)
    assert not any("rejected op" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_worker_start_faults_without_the_path_on_a_malformed_config(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A missing fence is the daemon's own file at fault, never the client."""
    config_dir = _broken_config(tmp_path, b'---\nworker_repos: "[]"\n---\n')
    ws = AsyncMock(spec=WebSocket)
    with (
        patch.object(
            WorkerRepoPolicy,
            "resolve",
            side_effect=_unfenced(config_dir / "vox.md"),
        ),
        caplog.at_level(logging.WARNING, logger="punt_vox.voxd.wire_reply"),
    ):
        await _worker_handlers(config_dir, tmp_path).handle(
            _start_request(tmp_path), ws
        )
    _assert_path_free_fault(ws.send_json.call_args.args[0], tmp_path, caplog)


@_UNREADABLE
@pytest.mark.asyncio
async def test_allowlist_rpcs_fault_without_the_path_on_an_unreadable_config(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    config_dir = _broken_config(tmp_path, b'---\nworker_repos: "[]"\n---\n')
    (config_dir / "vox.md").chmod(0)
    handlers = AllowlistHandlers(WorkerRepoPolicy(ConfigStore(config_dir))).handlers()
    ws = AsyncMock(spec=WebSocket)
    repo = tmp_path / "repo"
    repo.mkdir()
    try:
        for operation in ("repos", "allow", "disallow"):
            caplog.clear()
            with (
                patch(
                    "punt_vox.voxd.workers.policy.subprocess.run",
                    return_value=MagicMock(stdout=f"{repo.resolve()}\n"),
                ),
                caplog.at_level(logging.WARNING, logger="punt_vox.voxd.wire_reply"),
            ):
                await handlers[f"worker_{operation}"](
                    {"type": f"worker_{operation}", "id": "r", "project": str(repo)},
                    ws,
                )
            _assert_path_free_fault(ws.send_json.call_args.args[0], tmp_path, caplog)
    finally:
        (config_dir / "vox.md").chmod(0o600)


@pytest.mark.asyncio
async def test_allowlist_rpcs_fault_without_the_path_on_a_malformed_config(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """No closing fence: a fault with the path in vox.log, never an error frame."""
    config_dir = _broken_config(tmp_path, b"---\nworker_repos: \"['/repo']\"\n")
    handlers = AllowlistHandlers(WorkerRepoPolicy(ConfigStore(config_dir))).handlers()
    ws = AsyncMock(spec=WebSocket)
    repo = tmp_path / "repo"
    repo.mkdir()
    requests: tuple[dict[str, object], ...] = (
        {"type": "worker_allow", "id": "a", "project": str(repo)},
        {"type": "worker_disallow", "id": "d", "project": "/repo"},
    )
    for request in requests:
        caplog.clear()
        with (
            patch(
                "punt_vox.voxd.workers.policy.subprocess.run",
                return_value=MagicMock(stdout=f"{repo.resolve()}\n"),
            ),
            caplog.at_level(logging.WARNING, logger="punt_vox.voxd.wire_reply"),
        ):
            await handlers[str(request["type"])](request, ws)
        _assert_path_free_fault(ws.send_json.call_args.args[0], tmp_path, caplog)
