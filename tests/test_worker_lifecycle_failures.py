"""Lifecycle failures must stay isolated and cleanup must remain retryable."""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from starlette.websockets import WebSocket
from test_workers import FakeWorkerProvider, TrivialProfile, _fake_policy, _registry

from punt_vox.config import ConfigStore
from punt_vox.voxd.playback import PlaybackQueue
from punt_vox.voxd.workers.context import WorkerContext
from punt_vox.voxd.workers.git_failure import DiscardError
from punt_vox.voxd.workers.policy import WorkerRepoPolicy
from punt_vox.voxd.workers.records import WorkerRecords
from punt_vox.voxd.workers.registry import WorkerRegistry
from punt_vox.voxd.workers.worktrees import WorkerWorktrees


def test_corrupt_worker_record_does_not_block_healthy_workers(tmp_path: Path) -> None:
    provider = FakeWorkerProvider()
    registry = _registry(tmp_path, provider)
    healthy = registry.start("edit", str(tmp_path), provider="fake", harness="test")
    bad = tmp_path / "workers" / f"{uuid4()}.json"
    bad.write_text("broken JSON")
    restored = _registry(tmp_path, provider)
    assert restored.list() == [healthy]
    assert bad.read_text() == "broken JSON"
    with patch("punt_vox.voxd.workers.wiring.user_state_dir", return_value=tmp_path):
        routes = WorkerContext.handlers(PlaybackQueue())
    assert "worker_list" in routes
    assert "hook_event" in routes


@pytest.mark.parametrize("field", ["provider", "harness"])
def test_unknown_catalog_entry_cannot_prevent_rebuild(
    tmp_path: Path, field: str
) -> None:
    provider = FakeWorkerProvider()
    registry = _registry(tmp_path, provider)
    record = registry.start("edit", str(tmp_path), provider="fake", harness="test")
    stale = (
        replace(record, provider="no-longer-installed")
        if field == "provider"
        else replace(record, harness="no-longer-installed")
    )
    WorkerRecords(tmp_path / "workers").save(stale)
    assert _registry(tmp_path, provider).list() == [stale]


def test_failed_start_preserves_original_error_and_cleans_trust(tmp_path: Path) -> None:
    provider = FakeWorkerProvider()
    registry = _registry(tmp_path, provider)
    with (
        patch.object(FakeWorkerProvider, "start", side_effect=OSError("launch failed")),
        patch.object(
            FakeWorkerProvider, "stop", side_effect=RuntimeError("stop failed")
        ),
        patch.object(TrivialProfile, "cleanup") as cleanup,
        pytest.raises(OSError, match="launch failed"),
    ):
        registry.start("edit", str(tmp_path), provider="fake", harness="test")
    cleanup.assert_called_once()
    payload = json.loads(next((tmp_path / "workers").glob("*.json")).read_text())
    assert payload["last_report"]["launch_error"] == "OSError"
    assert payload["last_report"]["stop_error"] == "RuntimeError"
    retried = registry.stop(registry.list()[0].id)
    assert retried.state == "stopped"
    assert "stop_error" not in retried.last_report


def test_rollback_checkpoint_failure_cannot_mask_launch_error(tmp_path: Path) -> None:
    provider = FakeWorkerProvider()
    provider.fail = True
    registry = _registry(tmp_path, provider)
    with (
        patch.object(WorkerRecords, "save", side_effect=[None, OSError("disk failed")]),
        pytest.raises(OSError, match="launch failed"),
    ):
        registry.start("edit", str(tmp_path), provider="fake", harness="test")
    assert registry.list()[0].state == "stopped"


def test_terminal_stop_retries_only_failed_cleanup(tmp_path: Path) -> None:
    provider = FakeWorkerProvider()
    registry = _registry(tmp_path, provider)
    record = registry.start("edit", str(tmp_path), provider="fake", harness="test")
    with patch.object(TrivialProfile, "cleanup", side_effect=TimeoutError("locked")):
        assert registry.stop(record.id).last_report["cleanup_error"] == "TimeoutError"
    with (
        patch.object(FakeWorkerProvider, "stop") as stop,
        patch.object(TrivialProfile, "cleanup") as cleanup,
    ):
        cleaned = registry.stop(record.id)
    cleanup.assert_called_once()
    stop.assert_not_called()
    assert cleaned.state == "stopped"
    assert "cleanup_error" not in cleaned.last_report


def test_dead_worker_is_persisted_and_explicit_stop_clears_pane(tmp_path: Path) -> None:
    provider = FakeWorkerProvider()
    registry = _registry(tmp_path, provider)
    record = registry.start("edit", str(tmp_path), provider="fake", harness="test")
    provider.alive = False
    with (
        patch.object(FakeWorkerProvider, "stop") as stop,
        patch.object(WorkerRecords, "save") as save,
    ):
        retired = registry.list()[0]
        assert retired.state == "exited"
        stop.assert_not_called()
        save.assert_any_call(retired)
        stopped = registry.stop(record.id)
        assert stopped.state == "exited"
        assert registry.stop(record.id).state == "exited"
    stop.assert_called_once_with(record.session)


@pytest.mark.asyncio
async def test_allowlist_routes_stay_live_when_worker_recovery_fails(
    tmp_path: Path,
) -> None:
    """The allowlist needs only the policy, so it outlives a failed recovery."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    policy = WorkerRepoPolicy(ConfigStore(config_dir))
    with (
        patch(
            "punt_vox.voxd.workers.context.WorkerSubsystem.policy", return_value=policy
        ),
        patch(
            "punt_vox.voxd.workers.context.WorkerSubsystem.recover",
            side_effect=RuntimeError("tmux gone"),
        ),
    ):
        handlers = WorkerContext.handlers(PlaybackQueue())
    ws = AsyncMock(spec=WebSocket)
    await handlers["worker_repos"]({"type": "worker_repos", "id": "r"}, ws)
    assert ws.send_json.call_args.args[0] == {
        "type": "worker_result",
        "id": "r",
        "result": [],
    }
    await handlers["worker_start"]({"type": "worker_start", "id": "s"}, ws)
    reply = ws.send_json.call_args.args[0]
    assert reply["type"] == "error"
    assert "recovery failed" in str(reply["message"])


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (
        ["init", "-q", "-b", "main"],
        [
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "root",
        ],
    ):
        subprocess.run(["git", "-C", str(repo), *args], check=True)
    return repo


@pytest.mark.subprocess
def test_timed_out_launch_discards_the_worktree_git_finished_anyway(
    tmp_path: Path,
) -> None:
    """Git completed after the deadline: nothing of value is in that worktree."""
    repo = _git_repo(tmp_path)
    policy = _fake_policy(repo, WorkerWorktrees())
    records = WorkerRecords(tmp_path / "workers")
    registry = WorkerRegistry(
        policy, records, {"fake": FakeWorkerProvider()}, {"test": TrivialProfile()}
    )
    real_run = subprocess.run

    def slow_git(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        done = real_run(argv, check=True, capture_output=True, text=True)
        if "add" in argv:  # git finished, but only after the deadline passed
            raise subprocess.TimeoutExpired(argv, 90)
        return done

    with (
        patch("punt_vox.voxd.workers.worktrees.subprocess.run", side_effect=slow_git),
        pytest.raises(
            ValueError,
            match="worker start timed out creating the worktree after 90 s",
        ),
    ):
        registry.start("edit", str(repo), provider="fake", harness="test")
    record = registry.list()[0]
    assert record.state == "stopped"
    assert record.last_report["launch_error"] == "ValueError"
    assert record.last_report["worktree_discarded"] is True
    assert not Path(record.worktree).exists()
    branches = subprocess.run(
        ["git", "-C", str(repo), "branch", "--list", record.branch],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert branches == ""
    assert not (tmp_path / "workers" / record.id).exists()


def _raise(exc: Exception) -> None:
    raise exc


def test_failed_launch_records_a_discard_failure(tmp_path: Path) -> None:
    provider = FakeWorkerProvider()
    provider.fail = True
    worktrees = MagicMock(spec=WorkerWorktrees)
    busy = DiscardError()
    busy.attempt("rmtree", lambda: _raise(OSError("busy")))
    worktrees.discard.side_effect = busy
    registry = _registry(tmp_path, provider, worktrees)
    with pytest.raises(OSError, match="launch failed"):
        registry.start("edit", str(tmp_path), provider="fake", harness="test")
    record = registry.list()[0]
    assert record.last_report["launch_error"] == "OSError"
    assert record.last_report["discard_error"] == "rmtree: OSError: busy"
    assert "worktree_discarded" not in record.last_report


class _StopRefusingProvider(FakeWorkerProvider):
    """A provider whose launch fails and whose pane then refuses to stop."""

    def stop(self, handle: str) -> None:
        raise RuntimeError("tmux kill-session refused")


def test_failed_stop_keeps_the_worktree_and_says_why(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A pane that may still be live keeps its checkout; the record says so."""
    provider = _StopRefusingProvider()
    provider.fail = True
    worktrees = MagicMock(spec=WorkerWorktrees)
    registry = _registry(tmp_path, provider, worktrees)
    with (
        caplog.at_level(logging.WARNING, logger="punt_vox.voxd.workers.worktrees"),
        pytest.raises(OSError, match="launch failed"),
    ):
        registry.start("edit", str(tmp_path), provider="fake", harness="test")
    record = registry.list()[0]
    worktrees.discard.assert_not_called()
    assert record.state == "starting"
    assert record.last_report["stop_error"] == "RuntimeError"
    assert record.last_report["discard_skipped"] == "provider stop failed"
    assert "worktree_discarded" not in record.last_report
    assert any(
        "worktree kept: provider stop failed" in r.getMessage() for r in caplog.records
    )
