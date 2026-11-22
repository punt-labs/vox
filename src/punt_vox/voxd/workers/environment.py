"""Transfer a worker environment through a private, single-use Unix socket."""

from __future__ import annotations

import json
import socket
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Self, final

__all__ = ["WorkerEnvironment"]


@final
class WorkerEnvironment:
    """Keep credential values out of process arguments and filesystem contents."""

    __slots__ = ("_directory", "_socket")
    _directory: tempfile.TemporaryDirectory[str]
    _socket: socket.socket

    def __new__(cls) -> Self:
        """Listen inside a private directory for one bootstrap connection."""
        self = super().__new__(cls)
        self._directory = tempfile.TemporaryDirectory(prefix="vox-env-")
        self._socket = socket.socket(socket.AF_UNIX)
        self._socket.settimeout(15)
        self._socket.bind(str(self.address))
        self._socket.listen(1)
        return self

    @property
    def address(self) -> Path:
        """Return the socket address, not an environment file."""
        return Path(self._directory.name) / "socket"

    def send(self, environment: Mapping[str, str]) -> None:
        """Deliver only to the process that can access the owner-only directory."""
        connection, _ = self._socket.accept()
        with connection:
            connection.sendall(json.dumps(dict(environment)).encode())

    def close(self) -> None:
        """Remove the socket even if launching or delivery failed."""
        self._socket.close()
        self._directory.cleanup()
