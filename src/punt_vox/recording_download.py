"""Stage a verified recording before publishing it under its store name."""

from __future__ import annotations

import contextlib
import errno
import os
import shutil
import stat
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Self, final

if TYPE_CHECKING:
    from collections.abc import Callable, Generator
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
        with self._staging() as (staged, dir_fd):
            count = fetch(staged)
            staged.flush()
            self._publish(staged, dir_fd)
            return count

    @contextlib.contextmanager
    def _staging(self) -> Generator[tuple[IO[bytes], int | None]]:
        """Name the staging file only in a directory that is actually private."""
        if self._parent_protects_entries():
            with self._staging_directory() as dir_fd:
                info = os.fstat(dir_fd)
                if info.st_uid == os.geteuid() and info.st_mode & 0o077 == 0:
                    with self._named_staging(dir_fd) as staged:
                        yield staged, dir_fd
                    return
        # Not all filesystems honor private permissions on new directories.
        with tempfile.TemporaryFile(dir=self._dest.parent) as staged:
            yield staged, None

    def _parent_protects_entries(self) -> bool:
        """Only use named staging when peers cannot replace its directory."""
        info = self._dest.parent.stat()
        return info.st_uid in {0, os.geteuid()} and (
            info.st_mode & 0o022 == 0 or bool(info.st_mode & stat.S_ISVTX)
        )

    @contextlib.contextmanager
    def _staging_directory(self) -> Generator[int]:
        """Keep the staging directory open for relative file operations."""
        path = Path(tempfile.mkdtemp(dir=self._dest.parent, prefix=".vox-rec-"))
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            yield fd
        finally:
            try:
                self._remove_empty_staging_directory(path, os.fstat(fd))
            finally:
                os.close(fd)

    @staticmethod
    @contextlib.contextmanager
    def _named_staging(dir_fd: int) -> Generator[IO[bytes]]:
        """Create and unlink only our own file, relative to the same directory."""
        flags = os.O_CREAT | os.O_EXCL | os.O_RDWR
        fd = os.open(_STAGED_FILE, flags, 0o600, dir_fd=dir_fd)
        try:
            with os.fdopen(fd, "w+b") as staged:
                yield staged
        finally:
            os.unlink(_STAGED_FILE, dir_fd=dir_fd)

    @staticmethod
    def _remove_empty_staging_directory(path: Path, original: os.stat_result) -> None:
        """Remove the empty directory only if its name still matches."""
        try:
            if os.path.samestat(path.lstat(), original):
                path.rmdir()
        except OSError as exc:
            if exc.errno not in {
                errno.EEXIST,
                errno.ENOENT,
                errno.ENOTDIR,
                errno.ENOTEMPTY,
            }:
                raise

    def _publish(self, staged: IO[bytes], dir_fd: int | None) -> None:
        """Give the verified inode a final name, copying only if links fail."""
        if dir_fd is not None:
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
