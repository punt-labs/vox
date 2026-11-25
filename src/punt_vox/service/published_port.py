"""The port voxd published in ``serve.port``, and whether it is this user's.

``vox daemon status`` may ask voxd for health before ``lsof``/``fuser`` have
attributed the port, but only through a port voxd itself published: the
``serve.port`` run file, which voxd writes by atomic rename when it binds.
This class reads that hint once and says whether it names the service port
and belongs to the calling user; when it does not, it says which of those
failed, so the reading never reports a port it did not read. It never takes
voxd's lock and never changes the file.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Self, final

from punt_vox.service.not_asked import NotAskedError
from punt_vox.service.stale_port_file import StalePortFile

if TYPE_CHECKING:
    from pathlib import Path

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
            PortProbeError: If the file is there but cannot be read.
        """
        port = StalePortFile.read_hint(path)
        try:
            st = path.stat()
        except FileNotFoundError:
            return cls(None, absent=True)
        except OSError as exc:
            return cls(
                port, owner=NotAskedError.unreadable(f"cannot stat {path}: {exc}")
            )
        if st.st_uid != os.getuid():
            return cls(port, owner=NotAskedError.not_owned(st.st_uid))
        return cls(port)

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
