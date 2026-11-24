"""Stage a verified recording before publishing it under its store name."""

from __future__ import annotations

import errno
import os
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Self, final

if TYPE_CHECKING:
    from collections.abc import Callable
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
        """Publish only after fetch verification and successful close."""
        # Closing flushes the data; exiting the context unlinks the staging file.
        with tempfile.NamedTemporaryFile(
            dir=self._dest.parent, prefix=".vox-rec-", delete_on_close=False
        ) as staged:
            count = fetch(staged)
            staged.close()
            self._publish(Path(staged.name))
            return count

    def _publish(self, staged: Path) -> None:
        """Land without clobbering, even on a filesystem without hard links."""
        try:
            self._dest.hardlink_to(staged)
        except OSError as exc:
            if exc.errno not in (errno.ENOSYS, errno.ENOTSUP, errno.EPERM, errno.EXDEV):
                raise
            with staged.open("rb") as source:
                self._copy_exclusive(source)

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
