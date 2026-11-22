"""Real git worktrees and the dedicated tmux worker transport."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import pytest
from test_workers import TrivialProfile

from punt_vox.config import ConfigStore
from punt_vox.voxd.workers.contracts import WorkerLaunch
from punt_vox.voxd.workers.policy import WorkerRepoPolicy
from punt_vox.voxd.workers.tmux import TmuxWorkerProvider

pytestmark = pytest.mark.subprocess


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is required")
@pytest.mark.parametrize("words", ["x;", "x\\;", ";", "a ; b"])
def test_real_tmux_steering_preserves_semicolons(tmp_path: Path, words: str) -> None:
    handle = f"vox-verbatim-{uuid4()}"
    command = ["tmux", "-L", "vox-workers", "-f", "/dev/null"]
    subprocess.run(
        [
            *command,
            "new-session",
            "-d",
            "-s",
            handle,
            "-c",
            str(tmp_path),
            sys.executable,
            "-c",
            "import sys, time; from pathlib import Path; "
            "Path('ready').write_text('ready'); "
            "Path('received').write_text(sys.stdin.readline()); time.sleep(60)",
        ],
        check=True,
        capture_output=True,
    )
    try:
        _until_file(tmp_path / "ready", "ready")
        TmuxWorkerProvider().steer(
            handle, words, urgent=False, profile=TrivialProfile()
        )
        assert _until_file(tmp_path / "received", "\n") == words + "\n"
    finally:
        subprocess.run([*command, "kill-session", "-t", f"={handle}"], check=True)


def test_policy_requires_allowlisted_top_level_and_creates_worktree(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "--allow-empty",
            "-m",
            "initial",
        ],
        check=True,
        capture_output=True,
    )
    config = ConfigStore(tmp_path / "config")
    config.write_field("worker_repos", repr([str(repo)]))
    policy = WorkerRepoPolicy(config)
    assert policy.resolve(str(repo)) == repo.resolve()
    alias = tmp_path / "alias"
    alias.symlink_to(repo, target_is_directory=True)
    assert policy.resolve(str(alias)) == repo.resolve()
    child = repo / "child"
    child.mkdir()
    config.write_field("worker_repos", repr([str(child)]))
    with pytest.raises(ValueError, match="top level"):
        policy.resolve(str(child))
    config.write_field("worker_repos", "not a list")
    with pytest.raises(ValueError, match="list of repository paths"):
        policy.resolve(str(repo))
    worktree = tmp_path / "worker" / "worktree"
    policy.create(repo, worktree, "vox-worker/test")
    assert (worktree / ".git").is_file()
    branch = subprocess.run(
        ["git", "-C", str(worktree), "branch", "--show-current"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert branch.stdout.strip() == "vox-worker/test"


def _until_file(path: Path, contains: str) -> str:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if path.exists() and contains in (content := path.read_text()):
            return content
        time.sleep(0.02)
    raise AssertionError(f"{path} did not contain {contains!r}")


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is required")
def test_real_tmux_literal_steering_clean_env_and_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = TmuxWorkerProvider()
    profile = TrivialProfile()
    worker_id = str(uuid4())
    handle = f"vox-test-{worker_id}"
    monkeypatch.setenv("VOX_WORKER_SECRET_TEST", "must-not-leak")
    provider.start(WorkerLaunch(handle, worker_id, tmp_path, "task"), profile)
    try:
        assert provider.status(handle)
        environment = _until_file(tmp_path / "environment", "HOME=")
        assert "VOX_WORKER_SECRET_TEST" not in environment
        assert "must-not-leak" not in environment
        before = time.monotonic()
        provider.steer(
            handle, "literal $(touch injected); end", urgent=False, profile=profile
        )
        assert time.monotonic() - before >= profile.settle_delay
        assert "literal $(touch injected); end" in _until_file(
            tmp_path / "received", "end"
        )
        assert not (tmp_path / "injected").exists()
        pane = subprocess.run(
            [
                "tmux",
                "-L",
                "vox-workers",
                "display-message",
                "-p",
                "-t",
                f"={handle}:",
                "#{pane_pid}",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        pid = int(pane.stdout.strip())
    finally:
        provider.stop(handle)
    assert not provider.status(handle)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.02)
    else:
        pytest.fail("worker pane process survived stop")
    provider.stop(handle)
