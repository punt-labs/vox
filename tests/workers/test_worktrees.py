"""A launch that never started leaves no worktree and no branch behind."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from punt_vox.voxd.workers.worktrees import WorkerWorktrees

pytestmark = pytest.mark.subprocess


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(
        repo,
        "-c",
        "user.email=t@t",
        "-c",
        "user.name=t",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "root",
    )
    return repo


def test_discard_removes_the_worker_worktree_and_branch(
    repo: Path, tmp_path: Path
) -> None:
    worker_id = str(uuid4())
    worktree = tmp_path / "workers" / worker_id / "worktree"
    WorkerWorktrees().create(repo, worktree, f"vox-worker/{worker_id}")
    assert worktree.is_dir()
    WorkerWorktrees().discard(repo, worktree, worker_id)
    assert not worktree.exists()
    assert _git(repo, "branch", "--list", f"vox-worker/{worker_id}") == ""
    assert "worktree" not in _git(repo, "worktree", "list").split("\n", 1)[1:]


def test_discard_with_nothing_created_is_a_no_op(repo: Path, tmp_path: Path) -> None:
    worker_id = str(uuid4())
    WorkerWorktrees().discard(
        repo, tmp_path / "workers" / worker_id / "worktree", worker_id
    )
    assert _git(repo, "branch", "--list") == "* main"


def test_discard_removes_a_half_made_worktree_directory(
    repo: Path, tmp_path: Path
) -> None:
    worker_id = str(uuid4())
    worktree = tmp_path / "workers" / worker_id / "worktree"
    worktree.mkdir(parents=True)
    (worktree / "partial").write_text("")
    _git(repo, "branch", f"vox-worker/{worker_id}")
    WorkerWorktrees().discard(repo, worktree, worker_id)
    assert not worktree.exists()
    assert _git(repo, "branch", "--list", f"vox-worker/{worker_id}") == ""


@pytest.mark.parametrize(
    "path",
    [
        Path("/somewhere/else/worktree"),
        Path("/workers/{id}/src"),
        Path("/workers/{id}"),
    ],
)
def test_discard_refuses_a_path_that_is_not_the_workers_own_worktree(
    tmp_path: Path, path: Path
) -> None:
    worker_id = str(uuid4())
    target = Path(str(path).replace("{id}", worker_id))
    with pytest.raises(ValueError, match="own worktree"):
        WorkerWorktrees().discard(tmp_path, target, worker_id)


def test_discard_refuses_a_malformed_worker_id(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        WorkerWorktrees().discard(tmp_path, tmp_path / "main" / "worktree", "main")


def _worktree_listing(repo: Path) -> str:
    return _git(repo, "worktree", "list", "--porcelain")


def _is_listed(repo: Path, worktree: Path) -> bool:
    listing = _worktree_listing(repo)
    return any(
        f"worktree {path}\n" in listing + "\n"
        for path in (worktree, worktree.resolve())
    )


def test_discard_leaves_a_missing_sibling_worktree_registered(
    repo: Path, tmp_path: Path
) -> None:
    """Discard never prunes the repository: another checkout whose directory is
    away for a moment (a mount, a move) keeps its registration."""
    mine, other = str(uuid4()), str(uuid4())
    my_tree = tmp_path / "workers" / mine / "worktree"
    other_tree = tmp_path / "workers" / other / "worktree"
    WorkerWorktrees().create(repo, my_tree, f"vox-worker/{mine}")
    WorkerWorktrees().create(repo, other_tree, f"vox-worker/{other}")
    shutil.rmtree(other_tree)
    WorkerWorktrees().discard(repo, my_tree, mine)
    assert not _is_listed(repo, my_tree)
    assert _is_listed(repo, other_tree)


def test_discard_unregisters_its_own_worktree_whose_directory_is_gone(
    repo: Path, tmp_path: Path
) -> None:
    worker_id = str(uuid4())
    worktree = tmp_path / "workers" / worker_id / "worktree"
    WorkerWorktrees().create(repo, worktree, f"vox-worker/{worker_id}")
    shutil.rmtree(worktree)
    WorkerWorktrees().discard(repo, worktree, worker_id)
    assert not _is_listed(repo, worktree)
    assert _git(repo, "branch", "--list", f"vox-worker/{worker_id}") == ""


def test_discard_removes_a_worktree_still_locked_from_its_creation(
    repo: Path, tmp_path: Path
) -> None:
    """A run killed at the deadline leaves git's own "initializing" lock behind."""
    worker_id = str(uuid4())
    worktree = tmp_path / "workers" / worker_id / "worktree"
    branch = f"vox-worker/{worker_id}"
    _git(
        repo,
        "worktree",
        "add",
        "--lock",
        "--reason",
        "initializing",
        "-b",
        branch,
        str(worktree),
    )
    WorkerWorktrees().discard(repo, worktree, worker_id)
    assert not worktree.exists()
    assert not _is_listed(repo, worktree)
    probe = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "show-ref",
            "--verify",
            "--quiet",
            f"refs/heads/{branch}",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert probe.returncode == 1
