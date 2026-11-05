"""Where voxd is: the host, port and token one connect is made with.

Explicit values win; otherwise the ``VOXD_*`` environment
(:class:`~punt_vox.client_env.DaemonEnv`); otherwise the run directory's
``serve.port`` / ``serve.token`` (:class:`RunFiles`), re-read on every
connect so a daemon restarted on a new port is found again.
"""

from __future__ import annotations

import ipaddress
from typing import TYPE_CHECKING, Self, final

from punt_vox.client_env import DaemonEnv
from punt_vox.client_errors import VoxdConnectionError, VoxdNotPublishedError
from punt_vox.paths import run_dir as _user_run_dir

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["RunFiles", "VoxdEndpoint"]


@final
class RunFiles:
    """Read voxd's run files: ``serve.port`` and ``serve.token``."""

    __slots__ = ()

    @staticmethod
    def port() -> int | None:
        """Return the daemon port from ``serve.port``; ``None`` if absent.

        Only absence is ``None``: no voxd published a port. A file that is
        there but cannot be read, or does not hold a port, raises
        ``VoxdConnectionError`` naming why -- a voxd may well be running.
        """
        port_file = _user_run_dir() / "serve.port"
        text = RunFiles._published(port_file)
        if text is None:
            return None
        if not text.isdecimal():
            msg = f"{port_file} does not hold a port: {text[:32]!r}"
            raise VoxdConnectionError(msg)
        return int(text)

    @staticmethod
    def _published(run_file: Path) -> str | None:
        """Return *run_file*'s stripped text; ``None`` only when it is absent."""
        try:
            return run_file.read_text().strip()
        except FileNotFoundError:
            return None
        except OSError as exc:
            msg = f"cannot read {run_file}: {exc.strerror or exc}"
            raise VoxdConnectionError(msg) from None

    @staticmethod
    def token() -> str | None:
        """Return the daemon auth token from ``serve.token``; ``None`` if absent."""
        token_file = _user_run_dir() / "serve.token"
        try:
            return token_file.read_text().strip()
        except (FileNotFoundError, OSError):
            return None


@final
class VoxdEndpoint:
    """The voxd a client connects to; resolves its URI afresh each time."""

    __slots__ = ("_host", "_port", "_token")

    _host: str
    _port: int | None  # None: from VOXD_PORT, else serve.port, on each connect
    _token: str | None  # None: from VOXD_TOKEN, else serve.token, on each connect

    def __new__(cls, host: str | None, port: int | None, token: str | None) -> Self:
        self = super().__new__(cls)
        self._host = host if host is not None else DaemonEnv.host()
        self._port = port
        self._token = token
        return self

    def uri(self) -> str:
        """Return the WebSocket URI, carrying the token in its query string.

        Raises ``VoxdNotPublishedError`` -- a refusal: nothing listens -- only
        when no port is given or set, ``serve.port`` is absent, and no
        ``VOXD_HOST`` names another host. Any other missing port is a plain
        ``VoxdConnectionError`` naming why.
        """
        uri = f"ws://{self._authority_host()}:{self._resolve_port()}/ws"
        token = self._resolve_token()
        if token:
            uri += f"?token={token}"
        return uri

    def _resolve_port(self) -> int:
        """Return the explicit port, else ``VOXD_PORT``, else ``serve.port``."""
        if self._port is not None:
            return self._port
        port = self._env_port() or RunFiles.port()
        if port is None:
            raise self._unpublished()
        return port

    @staticmethod
    def _env_port() -> int | None:
        """Return ``VOXD_PORT``; ``None`` when unset. A malformed one raises."""
        try:
            return DaemonEnv.port()
        except ValueError as exc:
            raise VoxdConnectionError(str(exc)) from None

    def _unpublished(self) -> VoxdConnectionError:
        """Return the error for no port anywhere: a refusal only for this host.

        The local ``serve.port`` says nothing about a daemon ``VOXD_HOST``
        names, so its absence there is no evidence that nothing listens.
        """
        if DaemonEnv.names_host():
            return VoxdConnectionError(
                f"no port for voxd at {self._host}: set VOXD_PORT (the local "
                "serve.port says nothing about another host)"
            )
        return VoxdNotPublishedError(
            "voxd is not running: no serve.port is published. "
            "Start it with: vox daemon start"
        )

    def _resolve_token(self) -> str | None:
        """Return the explicit token, else ``VOXD_TOKEN``, else ``serve.token``."""
        if self._token is not None:
            return self._token
        return DaemonEnv.token() or RunFiles.token()

    def _authority_host(self) -> str:
        """Return the host as a URI authority: an IPv6 literal goes in brackets.

        Unbracketed, ``ws://::1:8421/...`` is no URI at all. A zone stays as
        written (``[fe80::1%lo]``): websockets hands the bracketed text to
        ``getaddrinfo`` undecoded, so a ``%25``-encoded zone would not resolve.
        """
        try:
            literal = ipaddress.ip_address(self._host)
        except ValueError:
            return self._host
        return f"[{self._host}]" if literal.version == 6 else self._host
