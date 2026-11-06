"""Tests for :class:`GitIgnores`: git itself says which paths it ignores."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from punt_vox.voxd.conversation_mode.call_agent_errors import CallAgentSpawnError
from punt_vox.voxd.conversation_mode.git_ignores import GitIgnoreError, GitIgnores


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repo whose secrets are ignored only below the root, and locally."""
    root = tmp_path / "repo"
    (root / "sub").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "sub" / ".gitignore").write_text("creds.json\n*.txt\n!keep.txt\n")
    (root / ".git" / "info" / "exclude").write_text("private.md\n")
    return root


def _ignored(repo: Path, *paths: str) -> frozenset[str]:
    return GitIgnores.for_root(repo).ignored(paths)


def test_a_nested_gitignore_applies_in_its_directory(repo: Path) -> None:
    assert _ignored(repo, "sub/creds.json", "creds.json") == {"sub/creds.json"}


def test_a_negated_nested_pattern_is_not_ignored(repo: Path) -> None:
    assert _ignored(repo, "sub/notes.txt", "sub/keep.txt") == {"sub/notes.txt"}


def test_git_info_exclude_applies(repo: Path) -> None:
    assert _ignored(repo, "private.md") == {"private.md"}


def test_nothing_asked_nothing_ignored(repo: Path) -> None:
    assert _ignored(repo) == frozenset()


def test_a_work_tree_is_active(repo: Path) -> None:
    assert GitIgnores.for_root(repo).active


def test_a_directory_outside_any_repo_has_nothing_git_ignores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Outside a repository git honours no ignore file at all."""
    # git must not find the repo these tests run inside.
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    ignores = GitIgnores.for_root(tmp_path)
    assert (ignores.active, ignores.ignored(["creds.json"])) == (False, frozenset())


def test_no_git_fails_the_call_start(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", "")
    with pytest.raises(CallAgentSpawnError, match="git"):
        GitIgnores.for_root(repo)


def test_a_git_that_cannot_answer_raises(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ignores = GitIgnores.for_root(repo)
    monkeypatch.setenv("PATH", "")
    with pytest.raises(GitIgnoreError):
        ignores.ignored(["sub/creds.json"])
