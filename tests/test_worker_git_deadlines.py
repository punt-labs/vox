"""A stalled repository command must release the serialized worker actor."""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest

from punt_vox.config import ConfigStore
from punt_vox.voxd.workers.git_failure import DiscardError
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
        pytest.raises(DiscardError, match=r"^branch: CalledProcessError: "),
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
    assert error.startswith("branch: CalledProcessError: git -C <path> show-ref ")
    assert "worktree_discarded" not in record.last_report


def test_discard_failure_logs_the_command_and_scrubbed_stderr(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    worker_id = str(uuid4())
    failure = subprocess.CalledProcessError(
        128,
        ["git", "-C", "/Users/x/repo", "worktree", "remove", "--force", "/Users/x/y"],
        stderr="fatal: /Users/x/y\nhint: is it locked?\n",
    )
    refused = DiscardError()
    refused.attempt("remove", lambda: _raise(failure))
    with (
        caplog.at_level(logging.WARNING, logger="punt_vox.voxd.workers.worktrees"),
        patch.object(WorkerWorktrees, "discard", side_effect=refused),
    ):
        record = WorkerWorktrees().discard_recorded(_record(tmp_path, worker_id))
    line = next(r.getMessage() for r in caplog.records if worker_id in r.getMessage())
    assert "git -C <path> worktree remove --force <path>" in line
    assert "fatal: <path> hint: is it locked?" in line
    assert "/Users" not in line
    error = record.last_report["discard_error"]
    assert error == (
        "remove: CalledProcessError: git -C <path> worktree remove --force "
        "<path>: fatal: <path> hint: is it locked?"
    )


def _raise(exc: Exception) -> None:
    raise exc


def test_worktree_create_timeout_logs_its_elapsed_time(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    repo = tmp_path / "shiny-repo"
    repo.mkdir()
    with (
        caplog.at_level(logging.INFO, logger="punt_vox.voxd.workers.worktrees"),
        patch(
            "punt_vox.voxd.workers.worktrees.time.monotonic", side_effect=[0.0, 90.0]
        ),
        patch("subprocess.run", side_effect=subprocess.TimeoutExpired(["git"], 90)),
        pytest.raises(ValueError, match="timed out creating the worktree after 90 s"),
    ):
        WorkerWorktrees().create(repo, tmp_path / "worktree", "vox-worker/x")
    assert any(
        r.getMessage() == "Worker worktree for shiny-repo timed out after 90.0 s"
        for r in caplog.records
    )


def test_worktree_create_git_failure_logs_its_elapsed_time(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    repo = tmp_path / "shiny-repo"
    repo.mkdir()
    with (
        caplog.at_level(logging.INFO, logger="punt_vox.voxd.workers.worktrees"),
        patch("punt_vox.voxd.workers.worktrees.time.monotonic", side_effect=[0.0, 2.5]),
        patch(
            "subprocess.run",
            side_effect=subprocess.CalledProcessError(128, ["git"], stderr="fatal"),
        ),
        pytest.raises(ValueError, match="could not create the worktree: fatal"),
    ):
        WorkerWorktrees().create(repo, tmp_path / "worktree", "vox-worker/x")
    assert any(
        r.getMessage() == "Worker worktree for shiny-repo failed in 2.5 s"
        for r in caplog.records
    )


def _git_failing_at(
    worktree: Path, *steps: str
) -> tuple[Callable[..., subprocess.CompletedProcess[str]], list[list[str]]]:
    """Fake git: *worktree* is registered and its branch exists; the named
    verbs ("remove", "branch") exit 128. Returns the fake and its call log."""
    calls: list[list[str]] = []

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        verb = argv[4] if argv[3] == "worktree" else argv[3]
        if verb == "list":
            return subprocess.CompletedProcess(argv, 0, f"worktree {worktree}\n", "")
        if verb == "show-ref":
            return subprocess.CompletedProcess(argv, 0, "", "")
        if verb in steps:
            raise subprocess.CalledProcessError(
                128, argv, stderr=f"fatal: {verb} refused: /Users/x/y\n"
            )
        return subprocess.CompletedProcess(argv, 0, "", "")

    return run, calls


def _registered_record(tmp_path: Path, worker_id: str) -> WorkerRecord:
    """A record whose worktree path is what the fake listing reports."""
    return replace(
        _record(tmp_path, worker_id),
        project=str(tmp_path),
        worktree=str(tmp_path / worker_id / "worktree"),
    )


def test_discard_attempts_every_step_after_a_failed_remove(tmp_path: Path) -> None:
    worker_id = str(uuid4())
    record = _registered_record(tmp_path, worker_id)
    run, calls = _git_failing_at(Path(record.worktree), "remove")
    with patch("subprocess.run", side_effect=run):
        record = WorkerWorktrees().discard_recorded(record)
    assert ["branch", "-D", f"vox-worker/{worker_id}"] in [c[3:6] for c in calls]
    error = record.last_report["discard_error"]
    assert error == (
        "remove: CalledProcessError: git -C <path> worktree remove --force "
        "--force <path>: fatal: remove refused: <path>"
    )
    assert "worktree_discarded" not in record.last_report


def test_discard_records_every_failed_step(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    worker_id = str(uuid4())
    record = _registered_record(tmp_path, worker_id)
    run, _ = _git_failing_at(Path(record.worktree), "remove", "branch")
    with (
        caplog.at_level(logging.WARNING, logger="punt_vox.voxd.workers.worktrees"),
        patch("subprocess.run", side_effect=run),
    ):
        record = WorkerWorktrees().discard_recorded(record)
    error = record.last_report["discard_error"]
    assert isinstance(error, str)
    assert error.startswith("remove: CalledProcessError: ")
    assert "; branch: CalledProcessError: git -C <path> branch -D " in error
    assert "/Users" not in error
    line = next(r.getMessage() for r in caplog.records if worker_id in r.getMessage())
    assert "remove: CalledProcessError" in line
    assert "; branch: CalledProcessError" in line


def test_worktree_create_git_failure_is_a_spoken_value_error(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """git's reason reaches the agent; the repository's path reaches no one."""
    repo = tmp_path / "shiny-repo"
    repo.mkdir()
    failure = subprocess.CalledProcessError(
        128,
        ["git", "-C", str(repo), "worktree", "add", "-b", "vox-worker/x", "/w/x"],
        stderr=(
            f"fatal: a branch named 'vox-worker/x' already exists\nhint: see {repo}\n"
        ),
    )
    with (
        caplog.at_level(logging.WARNING, logger="punt_vox.voxd.workers.worktrees"),
        patch("subprocess.run", side_effect=failure),
        pytest.raises(ValueError) as raised,
    ):
        WorkerWorktrees().create(repo, tmp_path / "worktree", "vox-worker/x")
    assert str(raised.value) == (
        "worker start could not create the worktree: "
        "fatal: a branch named 'vox-worker/x' already exists"
    )
    assert isinstance(raised.value.__cause__, subprocess.CalledProcessError)
    line = next(r.getMessage() for r in caplog.records if "could not" in r.getMessage())
    assert "git -C <path> worktree add -b vox-worker/x <path>" in line
    assert "fatal: a branch named 'vox-worker/x' already exists" in line
    assert str(tmp_path) not in line


def test_worktree_create_git_failure_without_stderr_names_the_exit(
    tmp_path: Path,
) -> None:
    with (
        patch(
            "subprocess.run",
            side_effect=subprocess.CalledProcessError(128, ["git"], stderr=""),
        ),
        pytest.raises(
            ValueError,
            match=r"^worker start could not create the worktree: git exited 128$",
        ),
    ):
        WorkerWorktrees().create(tmp_path, tmp_path / "worktree", "vox-worker/x")
