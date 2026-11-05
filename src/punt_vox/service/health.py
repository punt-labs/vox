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

from typing import Self, final

from punt_vox.client_endpoint import RunFiles
from punt_vox.client_sync import VoxClientSync
from punt_vox.service.process import DEFAULT_PORT
from punt_vox.service.voxd_bind import VoxdBind

__all__ = ["DaemonNotUpError", "HealthTarget", "HolderGoneError"]


@final
class DaemonNotUpError(RuntimeError):
    """voxd has not written its token yet, so no health request is sent."""

    __slots__ = ()


@final
class HolderGoneError(RuntimeError):
    """The voxd attributed the port left it before the health request went out.

    Recorded as the probe's ``health_miss`` only: no token-bearing request
    was sent.
    """

    __slots__ = ()


@final
class HealthTarget:
    """Host, port, and token of the voxd instance the service manager runs."""

    __slots__ = ("_host", "_port")

    _host: str
    _port: int

    def __new__(cls, bind: str) -> Self:
        self = super().__new__(cls)
        # A name is refused here too (PortProbeError): a unit written before
        # install validated the bind can still carry one.
        self._host = VoxdBind(bind).poll_host()
        self._port = DEFAULT_PORT
        return self

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._port

    def client(self) -> VoxClientSync:
        """Return a client pinned to the daemon's host, port, and run-file token.

        Raises ``DaemonNotUpError`` when ``serve.token`` is absent: voxd writes
        it on startup, so the caller treats that as "not up yet" and polls.
        """
        token = RunFiles.token()
        if token is None:
            msg = "voxd has not written serve.token yet"
            raise DaemonNotUpError(msg)
        return VoxClientSync(host=self._host, port=self._port, token=token)
