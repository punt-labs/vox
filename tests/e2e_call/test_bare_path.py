"""The daemon's PATH under test resolves python and git, and nothing else."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

from .bare_path import BarePath


def test_path_is_one_directory_with_exactly_python_and_git(tmp_path: Path) -> None:
    path = BarePath(tmp_path)
    assert os.pathsep not in path.value
    assert path.names() == {"python", "git"}
    assert Path(path.value, "python").resolve() == Path(sys.executable).resolve()


def test_tmux_is_unreachable_on_the_bare_path(tmp_path: Path) -> None:
    path = BarePath(tmp_path)
    assert shutil.which("tmux", path=path.value) is None
    assert shutil.which("git", path=path.value) is not None


def test_a_missing_git_fails_loudly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    with pytest.raises(FileNotFoundError, match="git is not on PATH"):
        BarePath(tmp_path)
