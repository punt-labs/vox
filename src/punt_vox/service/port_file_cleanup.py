"""Clearing a stale ``serve.port``, and what one attempt to clear it did.

``stop`` clears the file once per run; it needs to know whether the attempt
settled the matter (removed, nothing to remove) or was declined because a
visible voxd holds the service port -- a decline leaves the cleanup to a
later tick.

The cleanup is pinned to the file it found: its identity (device and inode)
is read when the cleanup decides the daemon is stale, and the file is removed
only if it is still that one. The check and the unlink run under the same
sibling ``flock`` voxd holds while it publishes ``serve.port``, so a voxd
publishing at any point after the cleanup looked keeps its new file.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import TYPE_CHECKING, Self, final

from punt_vox.service.backend import PortProbeError
from punt_vox.sibling_lock import SiblingLock

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["PortFileCleanup", "StalePortFile"]


@final
class PortFileCleanup(Enum):
    """The result of clearing ``serve.port``."""

    REMOVED = "removed"
    ABSENT = "absent"
    # The file names another port: another voxd's, left alone.
    OTHER_PORT = "other_port"
    # A visible voxd holds the service port; the file may be its own.
    HELD = "held"
    # A voxd published the file after the cleanup looked: its own, left alone.
    REPLACED = "replaced"

    @property
    def settled(self) -> bool:
        """Return True when nothing is left to clear: only a decline is not."""
        return self is not PortFileCleanup.HELD


@final
class StalePortFile:
    """``serve.port`` as the CLI clears it, under the lock voxd publishes with."""

    __slots__ = ("_lock", "_path", "_seen")

    _path: Path
    _lock: SiblingLock
    _seen: tuple[int, int] | None  # (st_dev, st_ino) found; None: no file then

    def __new__(cls, path: Path, seen: tuple[int, int] | None) -> Self:
        self = super().__new__(cls)
        self._path = path
        self._seen = seen
        # The same sibling voxd's PortFile locks, so the two serialize.
        self._lock = SiblingLock(path)
        return self

    @classmethod
    def as_found(cls, path: Path) -> Self:
        """Return the cleanup pinned to the file at *path* now, if any."""
        return cls(path, cls._identity_of(path))

    def discard(self, port: int) -> PortFileCleanup:
        """Remove the file found when it names *port* or does not parse.

        An unparseable file is stale whoever wrote it (a crash leftover); a
        file naming another port belongs to another voxd and is left, as is a
        file published since the cleanup looked. Returns what it did. With no
        file there it returns ``ABSENT`` before taking the lock, so a host with
        no run directory gets none created. A lock, read or removal failure
        raises ``PortProbeError`` so the caller can report it.
        """
        if self._identity_of(self._path) is None:
            return PortFileCleanup.ABSENT
        try:
            with self._lock.held():
                return self._discard_held(port)
        except OSError as exc:  # TimeoutError: a voxd held the lock throughout
            msg = f"cannot lock {self._path}: {exc}"
            raise PortProbeError(msg) from exc

    def _discard_held(self, port: int) -> PortFileCleanup:
        """Check and remove the file; call with the lock held."""
        found = self._identity_of(self._path)
        if found is None:
            return PortFileCleanup.ABSENT
        if found != self._seen:
            logger.info("%s was published after the cleanup looked; left", self._path)
            return PortFileCleanup.REPLACED
        try:
            text = self._path.read_text()
        except OSError as exc:
            msg = f"cannot read {self._path}: {exc}"
            raise PortProbeError(msg) from exc
        if text.strip().isdecimal() and int(text) != port:
            return PortFileCleanup.OTHER_PORT
        try:
            self._path.unlink(missing_ok=True)
        except OSError as exc:
            msg = f"cannot remove {self._path}: {exc}"
            raise PortProbeError(msg) from exc
        logger.info("Removed stale port file %s", self._path)
        return PortFileCleanup.REMOVED

    @staticmethod
    def _identity_of(path: Path) -> tuple[int, int] | None:
        """Return *path*'s (device, inode); ``None`` when there is no file.

        Any other stat failure raises ``PortProbeError``.
        """
        try:
            st = path.stat()
        except FileNotFoundError:
            return None
        except OSError as exc:
            msg = f"cannot read {path}: {exc}"
            raise PortProbeError(msg) from exc
        return (st.st_dev, st.st_ino)
