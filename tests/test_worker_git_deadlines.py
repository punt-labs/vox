"""A stalled repository command must release the serialized worker actor."""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest

from punt_vox.config import ConfigStore
from punt_vox.voxd.workers.model import WorkerRecord
from punt_vox.voxd.workers.policy import WorkerRepoPolicy
from punt_vox.voxd.workers.worktrees import WorkerWorktrees


def _policy(tmp_path: Path) -> WorkerRepoPolicy:
    config = Mock(spec=ConfigStore)
    config.read_field.return_value = repr([str(tmp_path)])
    return WorkerRepoPolicy(config)


def test_worker_resolve_has_a_short_deadline(tmp_path: Path) -> None:
    with patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess([], 0, str(tmp_path), ""),
    ) as run:
        _policy(tmp_path).resolve(str(tmp_path))
    timeout = run.call_args.kwargs.get("timeout")
    assert timeout is not None, "worker Git subprocess has no deadline"
    assert 0 < timeout < 30


def test_worktree_create_waits_ninety_seconds_for_git_under_load(
    tmp_path: Path,
) -> None:
    with patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess([], 0, "", ""),
    ) as run:
        _policy(tmp_path).create(tmp_path, tmp_path / "worktree", "worker")
    assert run.call_args.kwargs.get("timeout") == 90
    assert WorkerWorktrees.CREATE_DEADLINE == 90


def test_worktree_create_logs_its_elapsed_time_with_the_repository_name(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    repo = tmp_path / "shiny-repo"
    repo.mkdir()
    with (
        caplog.at_level(logging.INFO, logger="punt_vox.voxd.workers.worktrees"),
        patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, "", ""),
        ),
    ):
        WorkerWorktrees().create(repo, tmp_path / "worktree", "vox-worker/x")
    record = next(r for r in caplog.records if "shiny-repo" in r.getMessage())
    assert record.levelno == logging.INFO
    assert "worktree" in record.getMessage()
    assert " s" in record.getMessage()


def test_worktree_create_past_its_deadline_is_a_spoken_value_error(
    tmp_path: Path,
) -> None:
    with (
        patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(["git"], 90),
        ),
        pytest.raises(
            ValueError,
            match=r"^worker start timed out creating the worktree after 90 s$",
        ),
    ):
        _policy(tmp_path).create(tmp_path, tmp_path / "worktree", "vox-worker/x")


def test_direct_worker_rpc_requires_an_absolute_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    monkeypatch.chdir(tmp_path)
    config = Mock(spec=ConfigStore)
    config.read_field.return_value = repr([str(project)])
    with (
        patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, str(project), ""),
        ),
        pytest.raises(ValueError, match="absolute"),
    ):
        WorkerRepoPolicy(config).resolve("repo")


def _record(tmp_path: Path, worker_id: str) -> WorkerRecord:
    return WorkerRecord(
        id=worker_id,
        provider="fake",
        harness="test",
        session=f"vox-worker-{worker_id}",
        project=str(tmp_path / "repo"),
        worktree=str(tmp_path / "workers" / worker_id / "worktree"),
        branch=f"vox-worker/{worker_id}",
        task="edit",
        state="stopped",
        created_at="2026-11-24T00:00:00+00:00",
        last_report={"launch_error": "OSError"},
    )


def _git_answering(
    show_ref: int,
) -> Callable[..., subprocess.CompletedProcess[str]]:
    """Fake git: no worktree is registered and ``show-ref`` exits *show_ref*."""

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if "show-ref" in argv:
            return subprocess.CompletedProcess(argv, show_ref, "", "")
        if kwargs.get("check") and argv[3:5] == ["branch", "-D"]:
            raise AssertionError("branch -D must not run")
        return subprocess.CompletedProcess(argv, 0, "", "")

    return run


def test_branch_probe_exit_one_means_absent(tmp_path: Path) -> None:
    worker_id = str(uuid4())
    with patch("subprocess.run", side_effect=_git_answering(1)) as run:
        WorkerWorktrees().discard(
            tmp_path, tmp_path / worker_id / "worktree", worker_id
        )
    verbs = [call.args[0][3:5] for call in run.call_args_list]
    assert ["branch", "-D"] not in verbs


def test_branch_probe_failure_raises_instead_of_reading_absent(
    tmp_path: Path,
) -> None:
    worker_id = str(uuid4())
    with (
        patch("subprocess.run", side_effect=_git_answering(128)),
        pytest.raises(subprocess.CalledProcessError),
    ):
        WorkerWorktrees().discard(
            tmp_path, tmp_path / worker_id / "worktree", worker_id
        )


def test_branch_probe_failure_is_recorded_as_a_discard_error(
    tmp_path: Path,
) -> None:
    worker_id = str(uuid4())
    with patch("subprocess.run", side_effect=_git_answering(128)):
        record = WorkerWorktrees().discard_recorded(_record(tmp_path, worker_id))
    error = record.last_report["discard_error"]
    assert isinstance(error, str)
    assert error.startswith("CalledProcessError")
    assert "worktree_discarded" not in record.last_report
