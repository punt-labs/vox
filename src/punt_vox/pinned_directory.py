"""Read and replace files in one directory opened without following a symlink."""

from __future__ import annotations

import contextlib
import errno
import os
import secrets
import stat
from typing import TYPE_CHECKING, Self, final

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

__all__ = ["PinnedDirectory"]


@final
class PinnedDirectory:
    """A directory held by file descriptor for a whole read-modify-write.

    A path check followed by a later path lookup leaves a window: whoever can
    rename the directory can swap in a symlink after the check and have the
    write land wherever it points. Opening the directory once with
    ``O_NOFOLLOW`` and addressing every member through that descriptor
    (``dir_fd``) closes it -- a rename after the open moves the directory the
    descriptor still names, and a member that is (or becomes) a symlink is
    refused rather than followed.
    """

    __slots__ = ("_fd", "_path")

    _fd: int
    _path: Path

    _DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    # O_NONBLOCK so a FIFO planted at the name cannot hang the open; it is then
    # refused by the regular-file check.
    _READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    _TEMP_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    _NEW_FILE_MODE = 0o644

    def __new__(cls, fd: int, path: Path) -> Self:
        self = super().__new__(cls)
        self._fd = fd
        self._path = path
        return self

    @classmethod
    @contextlib.contextmanager
    def opened(cls, path: Path) -> Generator[Self]:
        """Create *path* if absent, open it without following a symlink, and yield it.

        Raise ``ValueError`` when *path* is a symlink.
        """
        path.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(path, cls._DIR_FLAGS)
        except OSError as exc:
            cls._refuse_symlink(path, exc)
            raise
        try:
            yield cls(fd, path)
        finally:
            os.close(fd)

    @property
    def fd(self) -> int:
        """Return the directory's file descriptor, valid inside :meth:`opened`."""
        return self._fd

    def read_text(self, name: str) -> str:
        """Return member *name* decoded as UTF-8, or ``""`` when it does not exist.

        Raise ``ValueError`` when the member is a symlink or not a regular file;
        a non-UTF-8 file raises ``UnicodeDecodeError``.
        """
        try:
            fd = os.open(name, self._READ_FLAGS, dir_fd=self._fd)
        except FileNotFoundError:
            return ""
        except OSError as exc:
            self._refuse_symlink(self._path / name, exc)
            raise
        with os.fdopen(fd, "rb") as handle:
            self._require_regular(name, os.fstat(handle.fileno()).st_mode)
            return handle.read().decode("utf-8")

    def replace_text(self, name: str, text: str) -> None:
        """Atomically replace member *name* with *text*, keeping its mode.

        The temp file is created inside the pinned directory and renamed over
        *name* relative to the same descriptor, so both land in it. A new file
        gets :data:`_NEW_FILE_MODE`.
        """
        mode = self._existing_mode(name)
        temp = f".{name}.{secrets.token_hex(8)}.tmp"
        fd = os.open(temp, self._TEMP_FLAGS, 0o600, dir_fd=self._fd)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(text.encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
                os.fchmod(handle.fileno(), mode)
            os.replace(temp, name, src_dir_fd=self._fd, dst_dir_fd=self._fd)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(temp, dir_fd=self._fd)
            raise

    def _existing_mode(self, name: str) -> int:
        """Return regular member *name*'s permission bits, else the new-file mode."""
        try:
            st = os.stat(name, dir_fd=self._fd, follow_symlinks=False)
        except FileNotFoundError:
            return self._NEW_FILE_MODE
        self._require_regular(name, st.st_mode)
        return stat.S_IMODE(st.st_mode)

    def _require_regular(self, name: str, mode: int) -> None:
        """Raise ``ValueError`` unless *mode* describes a regular file."""
        if stat.S_ISLNK(mode):
            msg = f"{self._path / name}: refusing a symlinked file"
            raise ValueError(msg)
        if not stat.S_ISREG(mode):
            msg = f"{self._path / name}: not a regular file"
            raise ValueError(msg)

    @staticmethod
    def _refuse_symlink(path: Path, exc: OSError) -> None:
        """Raise ``ValueError`` when *exc* is an ``O_NOFOLLOW`` open hitting a symlink.

        A symlink fails with ``ELOOP`` (``EMLINK`` on FreeBSD), or ``ENOTDIR``
        when Linux combines ``O_NOFOLLOW`` with ``O_DIRECTORY``; any other
        error returns so the caller re-raises it unchanged.
        """
        if exc.errno in {errno.ELOOP, errno.EMLINK, errno.ENOTDIR}:
            msg = f"{path}: refusing a symlinked path"
            raise ValueError(msg) from exc
