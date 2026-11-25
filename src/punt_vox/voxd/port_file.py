"""voxd's ``serve.port`` and ``serve.pid``: written on bind, removed only while ours.

Every voxd for a user shares one run directory, and across a restart the old
and new daemons serve the same port, so the file's text cannot say whose it
is. Each write therefore lands as a new file (written beside it, then renamed
over it), and the daemon remembers that file's identity -- device and inode.
At shutdown it removes each file only if the file there is still the one it
wrote. A daemon that exits after another voxd has written its own files
leaves those alone, so clients keep finding the running daemon.

``serve.pid`` names the process that published ``serve.port``. The CLI's
status reading asks that process for health before any ``lsof`` attribution,
and only while the pid is alive, this user's, and running voxd: a port file
left by a crash cannot send the token to whatever binds the port next.

Publishing and the check-and-unlink both run under one exclusive ``flock`` on
a sibling lock file every voxd in the run directory shares, so no publish can
land between a removal's ownership check and its unlink.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, ClassVar, Self, final

from punt_vox.file_identity import FileIdentity
from punt_vox.sibling_lock import SiblingLock

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["PortFile"]


@final
class PortFile:
    """One daemon's claim on ``serve.port`` in its run directory."""

    __slots__ = ("_lock", "_mine", "_mine_pid", "_path", "_pid_path")

    _CREATE_FLAGS: ClassVar[int] = (
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    )

    _path: Path
    _pid_path: Path
    _lock: SiblingLock
    _mine: FileIdentity | None  # the port file written; None: never written
    _mine_pid: FileIdentity | None  # the pid file written; None: never written

    def __new__(cls, run_dir: Path) -> Self:
        """Create a port file."""
        self = super().__new__(cls)
        self._path = run_dir / "serve.port"
        self._pid_path = run_dir / "serve.pid"
        # A sibling, never the file itself: the rename swaps serve.port's
        # inode, and a lock on the old inode would serialize nothing.
        self._lock = SiblingLock(self._path)
        self._mine = None
        self._mine_pid = None
        return self

    def write(self, port: int) -> None:
        """Publish *port* and this process's pid as new files, remembered as ours.

        Each rename is atomic, so a reader never sees a half-written file. A
        write that fails removes its temporary file and raises.

        Raises:
            OSError: If the port file cannot be written or published safely.
        """
        pid = os.getpid()
        with self._lock.held():
            # The pid first: a reader that sees this daemon's serve.port then
            # never pairs it with a previous daemon's serve.pid.
            self._mine_pid = self._publish(self._pid_path, str(pid))
            try:
                self._mine = self._publish(self._path, str(port))
            except OSError:
                # The old port must not stay paired with this pid: with no
                # pid file at all, a reader asks nothing. The publish error
                # is the one the caller sees; a cleanup failure is logged.
                self._unpublish_pid()
                raise
        logger.info("Wrote port file: %s (port %d, pid %d)", self._path, port, pid)

    def _unpublish_pid(self) -> None:
        """Remove the pid file just written, after a failed port publish.

        Never raises: the caller is already raising the publish error, which
        must not be replaced. The pid file is no longer counted as this
        daemon's either way.
        """
        try:
            self._pid_path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Could not remove %s: %s", self._pid_path, exc)
        finally:
            self._mine_pid = None

    @classmethod
    def _publish(cls, path: Path, text: str) -> FileIdentity:
        """Write *text* beside *path*, rename it over *path*; return its identity.

        The identity comes from the open descriptor after the rename: never
        from *path*, where a second voxd may already have renamed its own file,
        and never from before the rename, which changes the ctime.

        The temporary file is created afresh, private, and never through a
        symlink: whatever sits at its path -- a crashed run's leftover, or a
        planted link -- is unlinked (a link itself, not its target), and the
        create fails rather than follow one that reappears. A write that
        fails removes it.
        """
        tmp = path.with_name(f".{path.name}.{os.getpid()}")
        tmp.unlink(missing_ok=True)
        try:
            fd = os.open(tmp, cls._CREATE_FLAGS, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                tmp.replace(path)
                return FileIdentity.of(os.fstat(handle.fileno()))
        except OSError:
            tmp.unlink(missing_ok=True)
            raise

    def remove(self) -> None:
        """Remove ``serve.port`` and ``serve.pid`` if still the files this daemon wrote.

        A file another voxd has written since -- the same port after a restart
        or a different one -- is left in place. Runs at shutdown, so it never
        raises: a lock, stat or unlink failure is logged naming the path.
        """
        try:
            with self._lock.held():
                self._remove_if_mine(self._path, self._mine)
                self._remove_if_mine(self._pid_path, self._mine_pid)
        except OSError as exc:  # TimeoutError (a wedged lock) is an OSError
            logger.warning("Could not remove %s: %s", self._path, exc)

    @staticmethod
    def _remove_if_mine(path: Path, mine: FileIdentity | None) -> None:
        """Unlink *path* when it is the file this daemon wrote (identity *mine*).

        Call with the lock held. A failure other than "already gone" raises
        ``OSError``.
        """
        found = FileIdentity.at(path)
        if found is None:
            return
        if found != mine:
            logger.info("%s now belongs to another voxd; left in place", path)
            return
        path.unlink(missing_ok=True)
        logger.info("Removed %s", path)
