"""Keep a per-user state file and its ancestor directories private.

Per-user state -- provider keys, spoken-text logs, the vibe proof trail -- must
never be group/other-readable. Two forces fight that: the process umask masks
``mkdir``/``os.open`` mode bits, and a *pre-existing* file or directory keeps
whatever looser permissions it already had. :class:`PrivateState` owns both
concerns for a single target path -- it creates every missing ancestor at
``0o700`` and forces the opened file to ``0o600`` on every open, so neither a
permissive umask nor a stale loose file leaves the state world-readable.

Every tighten is best-effort: a ``chmod`` we cannot perform -- a directory
another user owns in a shared setup -- is logged and swallowed, never allowed to
block the write it protects. Privacy is defense-in-depth here, not a
precondition for functioning.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Self, final

__all__ = ["PrivateState"]

logger = logging.getLogger(__name__)

_DIR_MODE = 0o700  # private per-user directory
_FILE_MODE = 0o600  # private per-user file


@final
class PrivateState:
    """The privacy guard for one per-user state file and its ancestor dirs."""

    __slots__ = ("_path",)

    _path: Path

    def __new__(cls, path: Path) -> Self:
        self = super().__new__(cls)
        self._path = path
        return self

    @property
    def path(self) -> Path:
        """Return the file this guards."""
        return self._path

    def ensure_private_tree(self) -> None:
        """Create every missing ancestor dir at 0o700 and tighten the parent.

        ``mkdir(mode=...)`` is masked by the umask, so a bare
        ``mkdir(parents=True)`` can leave a freshly created state root
        (``~/.punt-labs``, ``~/.punt-labs/vox``) group/other-traversable on a
        fresh install. This creates the chain from the nearest existing ancestor
        down and tightens each *newly created* directory, so the whole hierarchy
        under which the file lives is private -- not just its immediate parent.
        The parent is re-tightened whether or not this call created it, since two
        peer processes race to create it and the loser must still find it 0o700.
        """
        parent = self._path.parent
        for directory in self._missing_ancestors(parent):
            directory.mkdir(mode=_DIR_MODE, exist_ok=True)
            self._tighten_dir(directory)
        self._tighten_dir(parent)

    def open_private(self, flags: int) -> int:
        """Open the file with *flags*, force it to 0o600, and return the fd.

        ``os.open(mode=0o600)`` applies the mode only when it *creates* the
        file, so a pre-existing log left group/other-readable by a permissive
        umask or a prior run keeps those bits. An ``os.fchmod`` on the open fd
        re-tightens every open -- best-effort, since a chmod failure must not
        block the write any more than the directory hardening does.
        """
        fd = os.open(self._path, flags, _FILE_MODE)
        self._tighten_fd(fd)
        return fd

    def nearest_existing_ancestor(self) -> Path:
        """Return the closest existing directory above the (absent) file."""
        return next(p for p in self._path.parents if p.exists())

    def _missing_ancestors(self, parent: Path) -> list[Path]:
        """Return parent's not-yet-existing ancestors, outermost first."""
        missing: list[Path] = []
        for directory in (parent, *parent.parents):
            if directory.exists():
                break
            missing.append(directory)
        missing.reverse()
        return missing

    def _tighten_dir(self, directory: Path) -> None:
        """Best-effort chmod *directory* to 0o700; log and swallow failure."""
        try:
            directory.chmod(_DIR_MODE)
        except OSError as exc:
            logger.debug(
                "private-state: cannot tighten %s to 0o700: %s", directory, exc
            )

    def _tighten_fd(self, fd: int) -> None:
        """Best-effort chmod the open fd to 0o600; log and swallow failure."""
        try:
            os.fchmod(fd, _FILE_MODE)
        except OSError as exc:
            logger.debug(
                "private-state: cannot tighten %s to 0o600: %s", self._path, exc
            )
