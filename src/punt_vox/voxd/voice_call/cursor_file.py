"""Read and atomically replace a regular departure cursor file."""

from __future__ import annotations

import os
import stat
from contextlib import suppress
from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.voice_call.cursor_commit import CursorCommit

if TYPE_CHECKING:
    from pathlib import Path


@final
class CursorFile:
    """Keep the call cursor private and reject special files."""

    __slots__ = ("_path",)
    _path: Path

    def __new__(cls, path: Path) -> Self:
        """Bind the cursor path without touching it."""
        self = super().__new__(cls)
        self._path = path
        return self

    def read(self) -> str:
        """Read UTF-8 only if the opened file descriptor is regular."""
        fd = os.open(self._path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        with os.fdopen(fd, encoding="utf-8") as stream:
            self._require_regular(os.fstat(stream.fileno()).st_mode)
            return stream.read()

    def replace(self, text: str) -> None:
        """Write the cursor atomically without replacing a known special file."""
        with suppress(FileNotFoundError):
            self._require_regular(self._path.lstat().st_mode)
        CursorCommit.write(self._path, text)

    @staticmethod
    def _require_regular(mode: int) -> None:
        if not stat.S_ISREG(mode):
            raise ValueError("Call departure cursor is not a regular file")
