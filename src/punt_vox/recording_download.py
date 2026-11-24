"""Stage a verified recording before publishing it under its store name."""

from __future__ import annotations

import errno
import os
import shutil
import tempfile
from typing import TYPE_CHECKING, Self, final

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path
    from typing import IO


@final
class RecordingDownload:
    """Manage a local recording's temporary and final files."""

    __slots__ = ("_dest",)

    _dest: Path

    def __new__(cls, dest: Path) -> Self:
        """Create a download targeting *dest*."""
        self = super().__new__(cls)
        self._dest = dest
        return self

    def save(self, fetch: Callable[[IO[bytes]], int]) -> int:
        """Verify and flush the download before opening the final path."""
        if self._dest.exists(follow_symlinks=False):
            raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), self._dest)
        with tempfile.TemporaryFile(dir=self._dest.parent) as staged:
            count = fetch(staged)
            staged.seek(0)
            self._copy_exclusive(staged)
            return count

    def _copy_exclusive(self, staged: IO[bytes]) -> None:
        """Reserve the final name and remove it if copying fails."""
        fd = os.open(self._dest, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            with os.fdopen(fd, "wb") as handle:
                shutil.copyfileobj(staged, handle)
        except BaseException:
            # A user interrupt also needs to leave the name free for a retry.
            self._dest.unlink(missing_ok=True)
            raise
