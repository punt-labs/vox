"""Lifecycle failures must stay isolated and cleanup must remain retryable."""

from __future__ import annotations

import json
import logging
import subprocess
import time
from dataclasses import replace
from pathlib import Path
from threading import Event
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
from punt_vox.voxd.workers.startup import WorkerStartup
from punt_vox.voxd.workers.tmux import TmuxWorkerProvider
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
    assert payload["last_report"]["launch_error"] == "OSError: launch failed"
    assert payload["last_report"]["stop_error"] == "RuntimeError"
    retried = registry.stop(registry.list()[0].id)
    assert retried.state == "stopped"
    assert "stop_error" not in retried.last_report


def test_rollback_checkpoint_failure_cannot_mask_launch_error(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    provider = FakeWorkerProvider()
    provider.fail = True
    registry = _registry(tmp_path, provider)
    with (
        patch.object(WorkerRecords, "save", side_effect=[None, OSError("disk failed")]),
        caplog.at_level(logging.WARNING, logger="punt_vox.voxd.workers.registry"),
        pytest.raises(OSError, match="launch failed"),
    ):
        registry.start("edit", str(tmp_path), provider="fake", harness="test")
    record = registry.list()[0]
    assert record.state == "stopped"
    [warning] = [r for r in caplog.records if "rollback checkpoint" in r.getMessage()]
    assert record.id in warning.getMessage()
    assert "OSError: disk failed" in warning.getMessage()


def test_rollback_checkpoint_warning_scrubs_paths(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The message text reaches the log with every absolute path scrubbed."""
    provider = FakeWorkerProvider()
    provider.fail = True
    registry = _registry(tmp_path, provider)
    with (
        patch.object(
            WorkerRecords,
            "save",
            side_effect=[None, OSError(28, "no space", "/Users/x/private/file")],
        ),
        caplog.at_level(logging.WARNING, logger="punt_vox.voxd.workers.registry"),
        pytest.raises(OSError, match="launch failed"),
    ):
        registry.start("edit", str(tmp_path), provider="fake", harness="test")
    [warning] = [r for r in caplog.records if "rollback checkpoint" in r.getMessage()]
    assert "no space" in warning.getMessage()
    assert "/Users/x" not in warning.getMessage()
    assert "<path>" in warning.getMessage()


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


def _settled(registry: WorkerRegistry) -> None:
    """Wait for the background discard job to finish."""
    deadline = time.monotonic() + 30
    while not registry.cleanup_idle():
        assert time.monotonic() < deadline, f"never settled: {registry.list()}"
        time.sleep(0.01)


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
    real_popen = subprocess.Popen

    def slow_git(argv: list[str], **kwargs: object) -> subprocess.Popen[str]:
        process: subprocess.Popen[str] = real_popen(argv, **kwargs)  # type: ignore[call-overload]
        if "add" not in argv:  # the discard's own git calls run normally
            return process
        process.wait()  # git finished, but only after the deadline passed
        late = MagicMock(wraps=process)
        late.pid = process.pid
        late.__enter__.return_value = late
        late.communicate.side_effect = subprocess.TimeoutExpired(argv, 90)
        return late

    with (
        patch("punt_vox.voxd.workers.worktrees.subprocess.Popen", side_effect=slow_git),
        pytest.raises(
            ValueError,
            match="worker start timed out creating the worktree after 90 s",
        ),
    ):
        registry.start("edit", str(repo), provider="fake", harness="test")
    _settled(registry)
    record = registry.list()[0]
    assert record.state == "stopped"
    assert record.last_report["launch_error"] == (
        "ValueError: worker start timed out creating the worktree after 90 s"
    )
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
    """DiscardComplete (Z): a failed discard records its error, not done."""
    provider = FakeWorkerProvider()
    provider.fail = True
    worktrees = MagicMock(spec=WorkerWorktrees)
    busy = DiscardError()
    busy.attempt("rmtree", lambda: _raise(OSError("busy")))
    worktrees.discard.side_effect = busy
    registry = _registry(tmp_path, provider, worktrees)
    with pytest.raises(OSError, match="launch failed"):
        registry.start("edit", str(tmp_path), provider="fake", harness="test")
    _settled(registry)
    record = registry.list()[0]
    assert record.last_report["launch_error"] == "OSError: launch failed"
    assert record.last_report["discard_error"] == "rmtree: OSError: busy"
    assert "discard_pending" not in record.last_report
    assert "worktree_discarded" not in record.last_report


class _StopRefusingProvider(FakeWorkerProvider):
    """A provider whose launch fails and whose pane refuses to stop until told."""

    refuses: bool = True

    def stop(self, handle: str) -> None:
        if self.refuses:
            raise RuntimeError("tmux kill-session refused")
        super().stop(handle)


def test_failed_stop_keeps_the_worktree_and_says_why(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Rollback (Z): a pane that may still be live keeps its checkout.

    Invariant "discard after confirmed stop": a worktree is gone only when
    the provider stopped and the phase is terminal.
    """
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


def test_launch_error_records_the_message_not_only_the_type(tmp_path: Path) -> None:
    """The 90 s deadline and an empty task are both ValueError; the record tells."""
    worktrees = MagicMock(spec=WorkerWorktrees)
    worktrees.create.side_effect = ValueError(
        "worker start timed out creating the worktree after 90 s"
    )
    registry = _registry(tmp_path, FakeWorkerProvider(), worktrees)
    with pytest.raises(ValueError, match="timed out"):
        registry.start("edit", str(tmp_path), provider="fake", harness="test")
    record = registry.list()[0]
    assert record.last_report["launch_error"] == (
        "ValueError: worker start timed out creating the worktree after 90 s"
    )


def test_launch_error_message_has_its_paths_scrubbed(tmp_path: Path) -> None:
    worktrees = MagicMock(spec=WorkerWorktrees)
    worktrees.create.side_effect = OSError(f"[Errno 28] no space: '{tmp_path}'")
    registry = _registry(tmp_path, FakeWorkerProvider(), worktrees)
    with pytest.raises(OSError, match="no space"):
        registry.start("edit", str(tmp_path), provider="fake", harness="test")
    record = registry.list()[0]
    assert (
        record.last_report["launch_error"] == "OSError: [Errno 28] no space: '<path>'"
    )


def test_tmux_kill_failure_on_rollback_keeps_the_worktree(tmp_path: Path) -> None:
    """The real provider: a refused kill leaves state starting and the checkout."""
    worktrees = MagicMock(spec=WorkerWorktrees)
    policy = _fake_policy(tmp_path, worktrees)
    records = WorkerRecords(tmp_path / "workers")
    registry = WorkerRegistry(
        policy, records, {"tmux": TmuxWorkerProvider()}, {"test": TrivialProfile()}
    )
    with (
        patch.object(TmuxWorkerProvider, "start", side_effect=OSError("launch failed")),
        patch.object(
            TmuxWorkerProvider,
            "_run",
            side_effect=[
                subprocess.CompletedProcess([], 0, "", ""),
                subprocess.CompletedProcess([], 0, "123\n", ""),
                subprocess.CompletedProcess([], 1, "", "server exited unexpectedly"),
            ],
        ),
        patch("punt_vox.voxd.workers.tmux.os.getpgid", return_value=123),
        pytest.raises(OSError, match="launch failed"),
    ):
        registry.start("edit", str(tmp_path), provider="tmux", harness="test")
    record = registry.list()[0]
    worktrees.discard.assert_not_called()
    assert record.state == "starting"
    assert record.last_report["stop_error"] == "RuntimeError"
    assert record.last_report["discard_skipped"] == "provider stop failed"


def test_failed_launch_discards_its_worktree_in_the_background(
    tmp_path: Path,
) -> None:
    """Rollback (Z): the launch error returns before the discard runs.

    Invariant "discard off the lock": the launch error returns while the
    discard is still pending.
    """
    provider = FakeWorkerProvider()
    provider.fail = True
    worktrees = MagicMock(spec=WorkerWorktrees)
    release = Event()

    def blocking_discard(project: Path, worktree: Path, worker_id: str) -> None:
        if not release.wait(2):
            raise TimeoutError("discard ran before the launch error returned")

    worktrees.discard.side_effect = blocking_discard
    registry = _registry(tmp_path, provider, worktrees)
    with pytest.raises(OSError, match="launch failed"):
        registry.start("edit", str(tmp_path), provider="fake", harness="test")
    pending = registry.list()[0]
    assert pending.state == "stopped"
    assert pending.last_report["launch_error"] == "OSError: launch failed"
    assert pending.last_report["discard_pending"] is True
    assert "worktree_discarded" not in pending.last_report
    release.set()
    _settled(registry)
    done = registry.list()[0]
    assert done.last_report["worktree_discarded"] is True
    assert "discard_pending" not in done.last_report
    worktrees.discard.assert_called_once()


def test_later_stop_retries_a_skipped_discard(tmp_path: Path) -> None:
    """DiscardRetryOnStop (Z): a clean stop re-queues the discard and runs it.

    Invariant "discard retried on stop": a skipped discard is owed again once
    a later stop leaves the worker stopped.
    """
    provider = _StopRefusingProvider()
    provider.fail = True
    worktrees = MagicMock(spec=WorkerWorktrees)
    registry = _registry(tmp_path, provider, worktrees)
    with pytest.raises(OSError, match="launch failed"):
        registry.start("edit", str(tmp_path), provider="fake", harness="test")
    skipped = registry.list()[0]
    assert skipped.last_report["discard_skipped"] == "provider stop failed"
    worktrees.discard.assert_not_called()
    with patch.object(_StopRefusingProvider, "stop", FakeWorkerProvider.stop):
        retried = registry.stop(skipped.id)
    assert retried.state == "stopped"
    assert not any(k.startswith("discard_") for k in retried.last_report)
    assert retried.last_report["worktree_discarded"] is True
    worktrees.discard.assert_called_once_with(
        Path(skipped.project), Path(skipped.worktree), skipped.id
    )


def test_later_stop_retries_a_failed_discard(tmp_path: Path) -> None:
    """DiscardRetryOnStop (Z): a transient discard error is retried on stop.

    Invariant "discard retried on stop": a failed discard is owed again once
    a later stop leaves the worker stopped.
    """
    provider = FakeWorkerProvider()
    provider.fail = True
    worktrees = MagicMock(spec=WorkerWorktrees)
    busy = DiscardError()
    busy.attempt("rmtree", lambda: _raise(OSError("busy")))
    worktrees.discard.side_effect = busy
    registry = _registry(tmp_path, provider, worktrees)
    with pytest.raises(OSError, match="launch failed"):
        registry.start("edit", str(tmp_path), provider="fake", harness="test")
    _settled(registry)
    failed = registry.list()[0]
    assert failed.last_report["discard_error"] == "rmtree: OSError: busy"
    worktrees.discard.side_effect = None
    retried = registry.stop(failed.id)
    assert retried.state == "stopped"
    assert not any(k.startswith("discard_") for k in retried.last_report)
    assert retried.last_report["worktree_discarded"] is True
    assert worktrees.discard.call_count == 2


def test_unexpected_discard_error_keeps_the_cleanup_outcome(tmp_path: Path) -> None:
    """A RuntimeError out of the discard lands as discard_error on the record.

    The same settlement job ran harness cleanup first; an exception escaping
    the discard would lose that outcome too, leaving cleanup_pending although
    cleanup ran.
    """
    provider = FakeWorkerProvider()
    worktrees = MagicMock(spec=WorkerWorktrees)
    worktrees.discard.side_effect = RuntimeError("policy exploded")
    registry = _registry(tmp_path, provider, worktrees)
    provider.fail = True
    with pytest.raises(OSError, match="launch failed"):
        registry.start("edit", str(tmp_path), provider="fake", harness="test")
    _settled(registry)
    record = registry.list()[0]
    assert "cleanup_pending" not in record.last_report
    assert "discard_pending" not in record.last_report
    assert record.last_report["discard_error"] == "RuntimeError: policy exploded"


def test_a_restart_that_retires_a_launch_whose_discard_was_skipped_queues_it(
    tmp_path: Path,
) -> None:
    """DiscardRetryOnStopOfStopped (Z): a confirmed stop makes the discard owed.

    A rollback that could not stop the provider recorded discard_skipped and
    left the worker starting. A later daemon start that finds the pane dead
    retires the record to exited with the session stop still pending; the
    retire alone does not queue the discard, because a dead pane does not
    prove the session is gone. The terminate that runs the pending stop
    does, so the worktree and branch never stay behind for good and are
    never removed under a session the daemon has not confirmed stopped.
    """
    provider = _StopRefusingProvider()
    provider.fail = True
    worktrees = MagicMock(spec=WorkerWorktrees)
    first = _registry(tmp_path, provider, worktrees)
    with pytest.raises(OSError, match="launch failed"):
        first.start("edit", str(tmp_path), provider="fake", harness="test")
    skipped = first.list()[0]
    assert skipped.state == "starting"
    assert skipped.last_report["discard_skipped"] == "provider stop failed"
    worktrees.discard.assert_not_called()
    first.shutdown()
    provider.alive = False
    records = WorkerRecords(tmp_path / "workers")
    [saved] = records.load()
    retired = WorkerStartup._retire(saved, records)
    assert retired.state == "exited"
    assert retired.last_report["discard_skipped"] == "provider stop failed"
    assert retired.last_report["pane_stop_pending"] is True
    assert "discard_pending" not in retired.last_report
    restored = _registry(tmp_path, provider, worktrees)
    with pytest.raises(RuntimeError, match="kill-session refused"):
        restored.stop(retired.id)  # the session still will not stop: kept
    assert restored.list()[0].last_report["discard_skipped"] == "provider stop failed"
    worktrees.discard.assert_not_called()
    provider.refuses = False
    restored.stop(retired.id)
    _settled(restored)
    done = restored.list()[0]
    assert done.last_report["worktree_discarded"] is True
    assert not any(k.startswith("discard_") for k in done.last_report)
    worktrees.discard.assert_called_once_with(
        Path(skipped.project), Path(skipped.worktree), skipped.id
    )
