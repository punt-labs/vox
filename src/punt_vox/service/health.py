"""Resolve and reach the voxd instance for the post-install health poll."""

from __future__ import annotations

import ipaddress
import os
from typing import Self

from punt_vox.client import VoxClientSync, read_token_file
from punt_vox.service.process import DEFAULT_PORT
from punt_vox.service.systemd import SystemdBackend


class HealthTarget:
    """Resolve host, port, and token of the voxd instance just installed.

    The health poll must reach *the daemon the backend actually started* --
    never whatever the install shell's ``VOXD_*`` env vars point at. All
    three connection values are pinned from authoritative sources, bypassing
    ``VoxClientSync``'s env/run-file resolution: ``port`` is ``DEFAULT_PORT``
    (baked into every unit by ``_voxd_exec_args``); ``host`` is the bind the
    *backend* applied, gated as its unit gates it (``_effective_bind``);
    ``token`` is read from voxd's ``serve.token`` run file, never a stray
    shell ``VOXD_TOKEN``.
    """

    __slots__ = ("_host", "_port")

    _host: str
    _port: int

    def __new__(cls, platform: str) -> Self:
        self = super().__new__(cls)
        self._host = self._resolve_host(platform)
        self._port = DEFAULT_PORT
        return self

    @staticmethod
    def _effective_bind(platform: str) -> str:
        """Return the ``VOXD_BIND`` value the installed unit passes to voxd.

        The install-shell value is not necessarily what voxd receives, because
        each backend gates ``VOXD_BIND`` differently: systemd embeds it only
        when ``safe_systemd_value`` accepts it (a rejected value is dropped, so
        voxd binds its ``DEFAULT_HOST`` loopback); launchd embeds it verbatim
        when non-empty, with no gate. The gate runs on the *raw* env value to
        match the backends -- a trailing ``\\n`` fails ``safe_systemd_value``
        yet vanishes under ``.strip()``, so stripping before gating would
        disagree with the unit and resolve the wrong host.
        """
        raw = os.environ.get("VOXD_BIND")
        if not raw:
            return ""
        if platform == "linux" and not SystemdBackend.safe_systemd_value(raw):
            return ""
        return raw.strip()

    @classmethod
    def _resolve_host(cls, platform: str) -> str:
        """Map the unit's effective ``VOXD_BIND`` to a reachable health host.

        A wildcard bind (unspecified addresses, or unset/dropped) accepts
        loopback, so poll ``127.0.0.1``. A concrete bind is the only address
        voxd listens on -- poll it directly (loopback would false-fail). A
        non-IP value (e.g. a hostname) is polled as given.
        """
        bind = cls._effective_bind(platform)
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

        The token is read fresh from ``serve.token`` and passed explicitly, so
        ``VoxClientSync`` cannot fall back to a stray shell ``VOXD_TOKEN``.
        voxd writes ``serve.token`` on startup, so an early probe may read
        ``None``; the poll's retry loop tolerates that brief race.
        """
        return VoxClientSync(
            host=self._host,
            port=self._port,
            token=read_token_file(),
        )
