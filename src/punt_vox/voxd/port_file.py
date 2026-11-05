"""voxd's ``serve.port``: written on bind, removed on shutdown only while it is ours.

Every voxd for a user shares one run directory, and across a restart the old
and new daemons serve the same port, so the file's text cannot say whose it
is. Each write therefore lands as a new file (written beside it, then renamed
over it), and the daemon remembers that file's identity -- device and inode.
At shutdown it removes ``serve.port`` only if the file there is still the one
it wrote. A daemon that exits after another voxd has written its own file
leaves that file alone, so clients keep finding the running daemon.

Publishing and the check-and-unlink both run under one exclusive ``flock`` on
a sibling lock file every voxd in the run directory shares, so no publish can
land between a removal's ownership check and its unlink.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Self, final

from punt_vox.sibling_lock import SiblingLock

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["PortFile"]


@final
class PortFile:
    """One daemon's claim on ``serve.port`` in its run directory."""

    __slots__ = ("_lock", "_mine", "_path")

    _path: Path
    _lock: SiblingLock
    _mine: tuple[int, int] | None  # (st_dev, st_ino) written; None: never written

    def __new__(cls, run_dir: Path) -> Self:
        self = super().__new__(cls)
        self._path = run_dir / "serve.port"
        # A sibling, never the file itself: the rename swaps serve.port's
        # inode, and a lock on the old inode would serialize nothing.
        self._lock = SiblingLock(self._path)
        self._mine = None
        return self

    def write(self, port: int) -> None:
        """Publish *port* as a new file and remember it as this daemon's.

        The rename is atomic, so a reader never sees a half-written file. The
        identity is read from the temporary file before the rename: read from
        ``serve.port`` after it, a second voxd renaming its own file in between
        would be recorded as this daemon's. A write that fails removes its
        temporary file and raises.
        """
        tmp = self._path.with_name(f".serve.port.{os.getpid()}")
        with self._lock.held():
            try:
                tmp.write_text(str(port))
                st = tmp.stat()
                tmp.replace(self._path)
            except OSError:
                tmp.unlink(missing_ok=True)
                raise
        self._mine = (st.st_dev, st.st_ino)
        logger.info("Wrote port file: %s (port %d)", self._path, port)

    def remove(self) -> None:
        """Remove ``serve.port`` if it is still the file this daemon wrote.

        A file another voxd has written since -- the same port after a restart
        or a different one -- is left in place. Runs at shutdown, so it never
        raises: a lock, stat or unlink failure is logged naming the path.
        """
        try:
            with self._lock.held():
                removed = self._remove_if_mine()
        except OSError as exc:  # TimeoutError (a wedged lock) is an OSError
            logger.warning("Could not remove %s: %s", self._path, exc)
            return
        if removed:
            logger.info("Removed port file %s", self._path)

    def _remove_if_mine(self) -> bool:
        """Unlink ``serve.port`` when it is the file this daemon wrote.

        Returns whether it did. Call with the lock held. A failure other than
        "already gone" raises ``OSError``.
        """
        try:
            st = self._path.stat()
        except FileNotFoundError:
            return False
        if (st.st_dev, st.st_ino) != self._mine:
            logger.info("%s now belongs to another voxd; left in place", self._path)
            return False
        self._path.unlink(missing_ok=True)
        return True
