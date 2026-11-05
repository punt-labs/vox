"""Resolve and reach the voxd instance the service manager runs.

The health request carries the daemon's token in its URL, so every connection
value is pinned to a trusted source and none comes from the calling shell:

- **host** from the bind written into the installed unit or plist (the
  backend reports it), mapped to a reachable address;
- **port** always ``DEFAULT_PORT``, the port every unit runs voxd on;
- **token** only from voxd's ``serve.token`` run file. With no token file the
  client is never built, because a client built with no token falls back to
  the shell's ``VOXD_TOKEN`` and would send it to whatever holds the port.
"""

from __future__ import annotations

import ipaddress
from typing import Self, final

from punt_vox.client import read_token_file
from punt_vox.client_sync import VoxClientSync
from punt_vox.service.process import DEFAULT_PORT

__all__ = ["DaemonNotUpError", "HealthTarget"]


@final
class DaemonNotUpError(RuntimeError):
    """voxd has not written its token yet, so no health request is sent."""

    __slots__ = ()


@final
class HealthTarget:
    """Host, port, and token of the voxd instance the service manager runs."""

    __slots__ = ("_host", "_port")

    _host: str
    _port: int

    def __new__(cls, bind: str) -> Self:
        self = super().__new__(cls)
        self._host = self._resolve_host(bind)
        self._port = DEFAULT_PORT
        return self

    @staticmethod
    def _resolve_host(bind: str) -> str:
        """Map the unit's ``VOXD_BIND`` to a reachable health host.

        A wildcard bind (an unspecified address, or none) accepts loopback, so
        poll ``127.0.0.1``. A concrete bind is the only address voxd listens
        on -- poll it directly (loopback would false-fail). A non-IP value
        (e.g. a hostname) is polled as given.
        """
        bind = bind.strip()
        if not bind:
            return "127.0.0.1"
        try:
            if ipaddress.ip_address(bind).is_unspecified:  # 0.0.0.0, ::, ::0
                return "127.0.0.1"
        except ValueError:
            pass
        return bind

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
        token = read_token_file()
        if token is None:
            msg = "voxd has not written serve.token yet"
            raise DaemonNotUpError(msg)
        return VoxClientSync(host=self._host, port=self._port, token=token)
