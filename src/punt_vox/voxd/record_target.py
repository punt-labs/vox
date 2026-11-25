"""Probe a contained recording target and write it atomically."""

from __future__ import annotations

import contextlib
import errno
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Self, final

from punt_vox.voxd.data_root_boundary import relativize_to_data_root
from punt_vox.voxd.path_status import PathStatus

__all__ = ["RecordTarget", "RecordWrite"]


@dataclass(frozen=True, slots=True)
class RecordWrite:
    """The landed recording: its final path and byte count.

    ``byte_count`` is the size the daemon wrote, echoed to the client so the
    caller can assert the on-disk file matches (byte-correct delivery).
    """

    path: Path
    byte_count: int

    @property
    def wire_path(self) -> str:
        """Return the landed path relativized for the wire, never a host prefix."""
        rel = relativize_to_data_root(self.path)
        return str(rel.path) if rel is not None else self.path.name


@final
class RecordTarget:
    """Handle filesystem operations on an already-contained recording path."""

    __slots__ = ("_path",)

    _path: Path

    def __new__(cls, path: Path) -> Self:
        """Create a target from a path previously resolved by the store."""
        self = super().__new__(cls)
        self._path = path
        return self

    @property
    def path(self) -> Path:
        """Return the resolved destination."""
        return self._path

    def check_name(self) -> None:
        """Reject a name too long for this destination filesystem."""
        # Non-strict resolve() hides ENAMETOOLONG; stat applies the actual
        # filesystem's name rules and PathStatus treats a missing take as normal.
        try:
            PathStatus.of(self._path)
        except OSError as exc:
            if exc.errno != errno.ENAMETOOLONG:
                raise
            raise ValueError(
                "recording name is too long for the recordings filesystem"
            ) from exc

    def place(self, source: Path) -> RecordWrite:
        """Land the audio atomically, allowing a cross-filesystem source."""
        moved = self._move(source, self._path)
        return moved if moved is not None else self._copy(source, self._path)

    @staticmethod
    def _move(source: Path, dest: Path) -> RecordWrite | None:
        """Atomically rename *source* onto *dest*, or None when it can't (EXDEV).

        Only a cross-filesystem rename (``EXDEV``) warrants the copy fallback;
        for any other ``OSError`` (``EACCES``, ``ENOENT``, ...) the copy path
        would not help and would mask the real cause, so re-raise it.

        The byte count is read from the *source* before the rename: reading
        ``dest`` afterwards would report a concurrent same-name write's size, not
        the bytes this call landed (mirrors the cross-device copy path).
        """
        try:
            byte_count = source.stat().st_size
            source.replace(dest)
        except OSError as exc:
            if exc.errno == errno.EXDEV:
                return None  # cross-device -- caller falls back to copy
            raise
        # An ephemeral source may not be private; the copy path's mkstemp temp is
        # 0600, so match that here to keep the recording private.
        dest.chmod(0o600)
        return RecordWrite(path=dest, byte_count=byte_count)

    @staticmethod
    def _copy(source: Path, dest: Path) -> RecordWrite:
        """Copy *source* to a sibling temp then atomically rename onto *dest*.

        The byte count is taken from the temp *before* the rename (the commit
        point); the ephemeral-source cleanup afterwards is best-effort, so a
        failure past the commit never turns a completed write into a failure.
        """
        # Write THROUGH the descriptor mkstemp returned (0600, O_EXCL) -- never
        # close it and reopen the temp by name. The name could be swapped for a
        # symlink between close and reopen (TOCTOU); writing to the fd targets
        # the exact inode mkstemp created.
        fd, tmp_name = tempfile.mkstemp(dir=dest.parent, suffix=".mp3.tmp")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as dst, source.open("rb") as src:
                shutil.copyfileobj(src, dst)
            byte_count = tmp.stat().st_size
            tmp.replace(dest)  # commit point -- the write is complete after this
        except OSError:
            tmp.unlink(missing_ok=True)
            raise

        with contextlib.suppress(OSError):
            source.unlink(missing_ok=True)
        return RecordWrite(path=dest, byte_count=byte_count)
