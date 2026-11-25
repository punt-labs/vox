"""The port voxd published in ``serve.port``, and whether it is this user's.

``vox daemon status`` may ask voxd for health before ``lsof``/``fuser`` have
attributed the port, but only through a port voxd itself published: the
``serve.port`` run file, which voxd writes by atomic rename when it binds.
This class reads that hint once and says whether it names the service port
and belongs to the calling user; when it does not, it says which of those
failed, so the reading never reports a port it did not read.

The port and the owner come from one open descriptor: the bytes read and the
``fstat`` of the file they were read from. A separate ``stat`` of the path
could describe a file voxd renamed into place after the read, and then the
uid trusted would not be the uid of the bytes trusted. The reader never takes
voxd's lock and never changes the file.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Self, final

from punt_vox.client_port import PortText
from punt_vox.service.backend import PortProbeError
from punt_vox.service.not_asked import NotAskedError

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["PublishedPort"]


@final
class PublishedPort:
    """One reading of ``serve.port``: the port it names and whose file it is."""

    __slots__ = ("_absent", "_owner", "_port")

    _absent: bool
    _port: int | None  # None: no file, or a file that does not name a port
    _owner: NotAskedError | None  # None: this user's file; else why not established

    def __new__(
        cls,
        port: int | None,
        *,
        absent: bool = False,
        owner: NotAskedError | None = None,
    ) -> Self:
        """Create a reading: *port* as published; *owner* why the file is not ours."""
        self = super().__new__(cls)
        self._port = port
        self._absent = absent
        self._owner = owner
        return self

    @classmethod
    def at(cls, path: Path) -> Self:
        """Read the hint at *path*; an absent file publishes nothing.

        Raises:
            PortProbeError: If the file is there but cannot be read or statted.
        """
        read = cls._owned_text(path)
        if read is None:
            return cls(None, absent=True)
        text, uid = read
        try:
            port = PortText.parse(text)
        except ValueError as exc:
            logger.warning("Ignoring stale %s: %s", path, exc)
            return cls(None, owner=cls._owner_of(uid))
        return cls(port, owner=cls._owner_of(uid))

    @staticmethod
    def _owner_of(uid: int) -> NotAskedError | None:
        """Return why a file owned by *uid* is not this user's; ``None`` when it is."""
        return None if uid == os.getuid() else NotAskedError.not_owned(uid)

    @staticmethod
    def _owned_text(path: Path) -> tuple[str, int] | None:
        """Return *path*'s text and owner uid from one open file; ``None`` if absent.

        Raises:
            PortProbeError: If the file is there but cannot be read or statted.
        """
        try:
            with path.open("rb") as handle:
                try:
                    uid = os.fstat(handle.fileno()).st_uid
                except OSError as exc:
                    msg = f"cannot stat {path}: {exc}"
                    raise PortProbeError(msg) from exc
                text = handle.read().decode("utf-8").strip()
        except FileNotFoundError:
            return None
        except UnicodeDecodeError:
            msg = f"cannot read {path}: invalid UTF-8"
            raise PortProbeError(msg) from None
        except OSError as exc:
            msg = f"cannot read {path}: {exc}"
            raise PortProbeError(msg) from exc
        return text, uid

    @property
    def port(self) -> int | None:
        """Return the port the file named; ``None`` when none is published."""
        return self._port

    def unasked(self, service_port: int) -> NotAskedError | None:
        """Return why this reading may not be asked; ``None`` when it may.

        Only this user's own file naming *service_port* opens the gate; each
        other reading names its own reason.
        """
        if self._absent:
            return NotAskedError.unpublished()
        if self._port is None:
            return NotAskedError.unparseable()
        if self._port != service_port:
            return NotAskedError.other_port(self._port)
        return self._owner
