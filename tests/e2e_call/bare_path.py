"""A PATH holding exactly the two binaries a voxd under test may run."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Self, final

__all__ = ["BarePath"]


@final
class BarePath:
    """Build one directory of symlinks named ``python`` and ``git``, and no more.

    The daemon's worker launch must fail on every machine so a test never
    starts a real coding session; a PATH that merely omits a tmux directory
    is not enough, because on Linux git and tmux share ``/usr/bin``. The whole
    PATH is this one directory, so tmux is unreachable wherever it lives.
    """

    __slots__ = ("_dir",)
    _dir: Path

    def __new__(cls, parent: Path) -> Self:
        """Populate ``parent / "bin"`` with the two links; fail if git is absent."""
        git = shutil.which("git")
        if git is None:
            msg = "git is not on PATH; the daemon needs it for worktrees"
            raise FileNotFoundError(msg)
        self = super().__new__(cls)
        self._dir = parent / "bin"
        self._dir.mkdir()
        (self._dir / "python").symlink_to(Path(sys.executable).resolve())
        (self._dir / "git").symlink_to(Path(git).resolve())
        return self

    @property
    def value(self) -> str:
        """Return the PATH string: the one directory."""
        return str(self._dir)

    def names(self) -> frozenset[str]:
        """Return the names the PATH resolves."""
        return frozenset(entry.name for entry in self._dir.iterdir())
