"""Clearing a stale ``serve.port`` under the lock voxd publishes it with.

The cleanup is pinned to one read of the file: it opens ``serve.port`` once,
under the same sibling ``flock`` voxd holds while it publishes the file, and
takes both the file's identity (device, inode, mtime and ctime -- see
:class:`~punt_vox.file_identity.FileIdentity`) and the port it names from
that one open file. Whatever the caller then does with the port -- probe it,
kill its holder -- the file it later removes is exactly the file whose port
it acted on, and only if it is still that file: a voxd that publishes at any
point after the read keeps its new file.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Self, final

from punt_vox.file_identity import FileIdentity
from punt_vox.service.backend import PortProbeError
from punt_vox.service.port_file_cleanup import PortFileCleanup
from punt_vox.sibling_lock import SiblingLock

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["StalePortFile"]


@final
class StalePortFile:
    """``serve.port`` as the CLI read it once, and may clear it."""

    __slots__ = ("_lock", "_path", "_port", "_seen")

    _path: Path
    _lock: SiblingLock
    _seen: FileIdentity | None  # the file read; None: no file then
    _port: int | None  # the port it named; None: no file, or it did not parse

    def __new__(cls, path: Path, seen: FileIdentity | None, port: int | None) -> Self:
        self = super().__new__(cls)
        self._path = path
        self._seen = seen
        self._port = port
        # The same sibling voxd's PortFile locks, so the two serialize.
        self._lock = SiblingLock(path)
        return self

    @classmethod
    def read(cls, path: Path) -> Self:
        """Read the file at *path* once: its identity and port from one open file.

        Read under the lock voxd publishes with, so the read never sees a file
        half-replaced. With no file there, nothing is pinned and no lock is
        taken, so a host with no run directory gets none created. Any read
        failure but "absent" raises ``PortProbeError``.
        """
        if cls._identity_of(path) is None:
            return cls(path, None, None)
        try:
            with SiblingLock(path).held():
                return cls._read_open(path)
        except OSError as exc:  # TimeoutError: a voxd held the lock throughout
            msg = f"cannot read {path}: {exc}"
            raise PortProbeError(msg) from exc

    @classmethod
    def _read_open(cls, path: Path) -> Self:
        """Open *path*, fstat that descriptor, and read the port from it."""
        try:
            with path.open("rb") as handle:
                st = os.fstat(handle.fileno())
                text = handle.read().decode("utf-8", "replace").strip()
        except FileNotFoundError:
            return cls(path, None, None)
        port = int(text) if text.isdecimal() else None
        return cls(path, FileIdentity.of(st), port)

    @property
    def port(self) -> int | None:
        """Return the port the file named; ``None``: no file, or unparseable."""
        return self._port

    def discard(self, port: int) -> PortFileCleanup:
        """Remove the file read when it names *port* or does not parse.

        An unparseable file is stale whoever wrote it (a crash leftover); a
        file naming another port belongs to another voxd and is left, as is a
        file published since the read. Returns what it did. With no file there
        it returns ``ABSENT`` before taking the lock. A lock, stat or removal
        failure raises ``PortProbeError`` so the caller can report it.
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
        """Check and remove the file; call with the lock held.

        The file is still the one read, so the port read from it is its port.
        """
        found = self._identity_of(self._path)
        if found is None:
            return PortFileCleanup.ABSENT
        if found != self._seen:
            logger.info("%s was published after the cleanup read it; left", self._path)
            return PortFileCleanup.REPLACED
        if self._port is not None and self._port != port:
            return PortFileCleanup.OTHER_PORT
        try:
            self._path.unlink(missing_ok=True)
        except OSError as exc:
            msg = f"cannot remove {self._path}: {exc}"
            raise PortProbeError(msg) from exc
        logger.info("Removed stale port file %s", self._path)
        return PortFileCleanup.REMOVED

    @staticmethod
    def _identity_of(path: Path) -> FileIdentity | None:
        """Return the identity of the file at *path*; ``None`` when there is none.

        Any other stat failure raises ``PortProbeError``.
        """
        try:
            return FileIdentity.at(path)
        except OSError as exc:
            msg = f"cannot read {path}: {exc}"
            raise PortProbeError(msg) from exc
