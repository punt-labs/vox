"""Resolve and reach the voxd instance the service manager runs.

The health request carries the daemon's token in its URL, so every connection
value is pinned to a trusted source and none comes from the calling shell:

- **host** from the bind written into the installed unit or plist (the
  backend reports it): an IP address, never a name -- a name could resolve to
  an address voxd never bound, and its lookup is bounded by no deadline;
- **port** always ``DEFAULT_PORT``, the port every unit runs voxd on;
- **token** only from voxd's ``serve.token`` run file. With no token file the
  client is never built, because a client built with no token falls back to
  the shell's ``VOXD_TOKEN`` and would send it to whatever holds the port.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, final

from punt_vox.client_endpoint import RunFiles
from punt_vox.client_sync import VoxClientSync
from punt_vox.paths import run_dir
from punt_vox.service.not_asked import NotAskedError
from punt_vox.service.process import DEFAULT_PORT
from punt_vox.service.voxd_bind import VoxdBind

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["DaemonNotUpError", "HealthTarget"]


@final
class DaemonNotUpError(NotAskedError):
    """voxd's token is not available, so no health request is sent.

    A :class:`NotAskedError`: the client is never built, so nothing reached
    voxd, and a reading must say it was not asked rather than that it gave
    no answer.
    """

    __slots__ = ()

    @classmethod
    def missing(cls, token_file: Path) -> Self:
        """Return the miss for a ``serve.token`` voxd has not written."""
        return cls(
            f"{token_file} is absent: voxd has not published its token; no token sent"
        )

    @classmethod
    def unreadable(cls, token_file: Path) -> Self:
        """Return the miss for a ``serve.token`` that is there but cannot be read."""
        return cls(
            f"{token_file} cannot be read: voxd's token is not available; no token sent"
        )


@final
class HealthTarget:
    """Host, port, and token of the voxd instance the service manager runs."""

    __slots__ = ("_host", "_port")

    _host: str
    _port: int

    def __new__(cls, bind: str) -> Self:
        """Create a health target."""
        self = super().__new__(cls)
        # A name is refused here too (PortProbeError): a unit written before
        # install validated the bind can still carry one.
        self._host = VoxdBind(bind).poll_host()
        self._port = DEFAULT_PORT
        return self

    @property
    def host(self) -> str:
        """Return the daemon host to probe."""
        return self._host

    @property
    def port(self) -> int:
        """Return the daemon port to probe."""
        return self._port

    def client(self) -> VoxClientSync:
        """Return a client pinned to the daemon's host, port, and run-file token.

        Raises ``DaemonNotUpError`` naming the file when ``serve.token`` is
        absent (voxd writes it on startup, so a poll keeps waiting) or cannot
        be read.

        Raises:
            DaemonNotUpError: If the daemon's token is absent or unreadable.
        """
        token = RunFiles.token()
        if token is None:
            token_file = run_dir() / "serve.token"
            if token_file.exists():
                raise DaemonNotUpError.unreadable(token_file)
            raise DaemonNotUpError.missing(token_file)
        return VoxClientSync(host=self._host, port=self._port, token=token)
