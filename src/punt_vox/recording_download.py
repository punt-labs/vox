"""Stage a verified recording before publishing it under its store name."""

from __future__ import annotations

import errno
import os
import shutil
import stat
import tempfile
from typing import TYPE_CHECKING, Self, final

from punt_vox.pinned_directory import PinnedDirectory

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path
    from typing import IO

_STAGED_FILE = "recording"
_LINK_UNAVAILABLE = {
    errno.ENOSYS,
    errno.ENOTSUP,
    errno.EOPNOTSUPP,
    errno.EPERM,
    errno.EXDEV,
}


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
        with (
            tempfile.TemporaryDirectory(
                dir=self._dest.parent, prefix=".vox-rec-"
            ) as tmp,
            PinnedDirectory.opened(self._dest.parent / tmp) as directory,
            self._open_staged(directory.fd) as staged,
        ):
            count = fetch(staged)
            staged.flush()
            self._publish(staged, directory.fd)
            return count

    @staticmethod
    def _open_staged(dir_fd: int) -> IO[bytes]:
        """Open the staging file within the pinned, private directory."""
        if os.fstat(dir_fd).st_uid != os.geteuid():
            raise PermissionError(
                "recording staging directory is not owned by this user"
            )
        flags = os.O_CREAT | os.O_EXCL | os.O_RDWR
        return os.fdopen(os.open(_STAGED_FILE, flags, 0o600, dir_fd=dir_fd), "w+b")

    def _publish(self, staged: IO[bytes], dir_fd: int) -> None:
        """Give the verified inode a final name, copying only if links fail."""
        # Filesystems without POSIX permissions may ignore mkdtemp's mode.
        if stat.S_IMODE(os.fstat(dir_fd).st_mode) & 0o077 == 0:
            try:
                os.link(_STAGED_FILE, self._dest, src_dir_fd=dir_fd)
                return
            except OSError as exc:
                if exc.errno not in _LINK_UNAVAILABLE:
                    raise
        staged.seek(0)
        self._copy_exclusive(staged)

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
