"""Regression proofs for worker failure isolation and managed hook admission."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from threading import Event
from unittest.mock import patch

import pytest
from test_workers import FakeWorkerProvider, TrivialProfile, _registry

from punt_vox.hook_context_relay import HookContextRelay
from punt_vox.voxd.hook_context_store import HookContextStore
from punt_vox.voxd.workers.claude import ClaudeProfile
from punt_vox.voxd.workers.model import WorkerRecord
from punt_vox.voxd.workers.records import WorkerRecords
from punt_vox.voxd.workers.tmux import TmuxWorkerProvider
from punt_vox.voxd.workers.tmux_probe import TmuxProbe


def test_worker_environment_keeps_other_tool_credentials_private() -> None:
    profile = ClaudeProfile(HookContextStore())
    assert profile.environment(
        {
            "PATH": "/bin",
            "ANTHROPIC_API_KEY": "fake-claude-key",
            "GH_TOKEN": "fake-github-key",
            "GITHUB_PERSONAL_ACCESS_TOKEN": "fake-github-pat",
        }
    ) == {"PATH": "/bin", "ANTHROPIC_API_KEY": "fake-claude-key"}


def test_dead_worker_cleanup_cannot_block_registry_rebuild(tmp_path: Path) -> None:
    provider = FakeWorkerProvider()
    registry = _registry(tmp_path, provider)
    registry.start("edit", str(tmp_path), provider="fake", harness="test")
    provider.alive = False
    published = Event()
    save = WorkerRecords.save

    def save_outcome(records: WorkerRecords, record: WorkerRecord) -> None:
        save(records, record)
        if "cleanup_error" in record.last_report:
            published.set()

    with (
        patch.object(
            TrivialProfile, "cleanup", side_effect=ValueError("bad JSON")
        ) as cleanup,
        patch.object(WorkerRecords, "save", save_outcome),
    ):
        restored = _registry(tmp_path, provider)
        cleanup.assert_not_called()
        record = restored.list()[0]
        assert record.state == "exited"
        assert record.last_report["cleanup_pending"] is True
        assert published.wait(1)
        deadline = time.monotonic() + 1
        while record.id in restored._retirement._executor._active:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert restored.stop(record.id).last_report["cleanup_error"] == "ValueError"
        assert cleanup.call_count == 2
    cleaned = restored.stop(record.id)
    assert "cleanup_error" not in cleaned.last_report
    assert "cleanup_pending" not in cleaned.last_report
    assert WorkerRecords(tmp_path / "workers").load()[0] == cleaned


def test_provider_unavailable_cannot_block_registry_rebuild(tmp_path: Path) -> None:
    provider = FakeWorkerProvider()
    registry = _registry(tmp_path, provider)
    record = registry.start("edit", str(tmp_path), provider="fake", harness="test")
    with patch.object(FakeWorkerProvider, "status", side_effect=FileNotFoundError):
        restored = _registry(tmp_path, provider)
        assert restored.list()[0] == record
    provider.alive = False
    assert restored.list()[0].state == "exited"


def test_failed_first_save_creates_no_worktree(tmp_path: Path) -> None:
    registry = _registry(tmp_path, FakeWorkerProvider())
    with (
        patch.object(WorkerRecords, "save", side_effect=OSError("disk full")),
        patch.object(registry._policy, "create") as create,
    ):
        with pytest.raises(OSError, match="disk full"):
            registry.start("edit", str(tmp_path), provider="fake", harness="test")
        create.assert_not_called()
    assert registry.list() == []


@pytest.mark.parametrize("operation", ["status", "stop-pane", "stop-kill"])
def test_tmux_session_disappearing_during_control(operation: str) -> None:
    provider = TmuxWorkerProvider()
    results = [subprocess.CompletedProcess([], 0, "", "")]
    if operation == "stop-kill":
        results.append(subprocess.CompletedProcess([], 0, "123\n", ""))
    results.append(subprocess.CompletedProcess([], 1, "", "no such session"))

    def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = results.pop(0)
        if check and result.returncode:
            raise RuntimeError("Worker tmux operation failed")
        return result

    with (
        patch.object(TmuxWorkerProvider, "_run", side_effect=run),
        patch("os.getpgid", return_value=456),
        patch.object(TmuxProbe, "wait_for_exit") as finish,
    ):
        if operation == "status":
            assert provider.status("worker") is False
        else:
            provider.stop("worker")
        if operation == "stop-kill":
            finish.assert_called_once_with(456)


def test_tmux_no_current_target_checks_exact_session_absence() -> None:
    with patch.object(
        TmuxWorkerProvider,
        "_run",
        side_effect=[
            subprocess.CompletedProcess([], 1, "", "no current target\n"),
            subprocess.CompletedProcess([], 0, "worker-other\n", ""),
        ],
    ) as run:
        assert TmuxWorkerProvider().status("worker") is False
    run.assert_called_with("list-sessions", "-F", "#{session_name}", check=False)


def test_managed_worker_hooks_need_no_repository_marker(tmp_path: Path) -> None:
    provider = FakeWorkerProvider()
    registry = _registry(tmp_path, provider)
    record = registry.start("edit", str(tmp_path), provider="fake", harness="test")
    worktree = Path(record.worktree)
    worktree.mkdir(parents=True)
    payload: dict[str, object] = {"session_id": record.id, "cwd": str(worktree)}
    with (
        patch("punt_vox.hook_context_admission.find_repo_root", return_value=worktree),
        patch(
            "punt_vox.hook_context_admission.VoxMarker.is_present", return_value=False
        ),
    ):
        assert not HookContextRelay(payload).enabled()
        assert registry.admits_hook(record.id, str(worktree))
        assert not registry.admits_hook("unknown", str(worktree))
        assert not registry.admits_hook(record.id, str(tmp_path))
        registry.stop(record.id)
        assert not registry.admits_hook(record.id, str(worktree))
    assert not (worktree / ".punt-labs").exists()


@pytest.mark.parametrize("data", ["broken JSON", "[]", "{}"])
def test_invalid_managed_record_cannot_admit_hooks(tmp_path: Path, data: str) -> None:
    registry = _registry(tmp_path, FakeWorkerProvider())
    record = registry.start("edit", str(tmp_path), provider="fake", harness="test")
    (tmp_path / "workers" / f"{record.id}.json").write_text(data)
    rebuilt = _registry(tmp_path, FakeWorkerProvider())
    payload: dict[str, object] = {"session_id": record.id, "cwd": record.worktree}
    with (
        patch("punt_vox.hook_context_admission.find_repo_root", return_value=None),
        patch(
            "punt_vox.hook_context_admission.VoxMarker.is_present", return_value=False
        ),
    ):
        assert not HookContextRelay(payload).enabled()
        assert not rebuilt.admits_hook(record.id, record.worktree)
