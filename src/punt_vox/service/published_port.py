"""The port voxd published in ``serve.port``, and whether it is this user's.

``vox daemon status`` may ask voxd for health before ``lsof``/``fuser`` have
attributed the port, but only through a port voxd itself published: the
``serve.port`` run file, which voxd writes by atomic rename when it binds.
This class reads that hint once and says whether it names the service port
and belongs to the calling user. It never takes voxd's lock and never changes
the file.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Self, final

from punt_vox.service.stale_port_file import StalePortFile

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["PublishedPort"]


@final
class PublishedPort:
    """One reading of ``serve.port``: the port it names and its owner."""

    __slots__ = ("_owned", "_port")

    _port: int | None
    _owned: bool

    def __new__(cls, port: int | None, *, owned: bool) -> Self:
        """Create a reading: *port* as published, *owned* when the file is ours."""
        self = super().__new__(cls)
        self._port = port
        self._owned = owned
        return self

    @classmethod
    def at(cls, path: Path) -> Self:
        """Read the hint at *path*; an absent file publishes nothing.

        Raises:
            PortProbeError: If the file is there but cannot be read.
        """
        port = StalePortFile.read_hint(path)
        if port is None:
            return cls(None, owned=False)
        try:
            owned = path.stat().st_uid == os.getuid()
        except OSError:
            owned = False
        return cls(port, owned=owned)

    @property
    def port(self) -> int | None:
        """Return the port the file named; ``None`` when none is published."""
        return self._port

    def names_own_service(self, service_port: int) -> bool:
        """Return True when this user's own voxd published *service_port*."""
        return self._owned and self._port == service_port
