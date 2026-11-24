"""Daemon health reporting for voxd."""
# pyright: reportPrivateUsage=false
# Internal module within the voxd package -- cross-module private access is expected.

from __future__ import annotations

import os
import time
from collections.abc import Callable
from typing import Self

from punt_vox.logging_config import daemon_log_level
from punt_vox.paths import installed_version
from punt_vox.voxd.playback import PlaybackQueue


class DaemonHealth:
    """Own health-check state and payload construction for voxd."""

    __slots__ = (
        "_daemon_version",
        "_get_client_count",
        "_playback",
        "_port",
        "_start_time",
    )

    _daemon_version: str
    _get_client_count: Callable[[], int]
    _playback: PlaybackQueue
    _port: int
    _start_time: float

    def __new__(
        cls,
        playback: PlaybackQueue,
        get_client_count: Callable[[], int],
        port: int,
    ) -> Self:
        """Create a daemon health."""
        self = super().__new__(cls)
        self._playback = playback
        self._get_client_count = get_client_count
        self._start_time = time.monotonic()
        self._port = port
        self._daemon_version = installed_version()
        return self

    # -- Properties ----------------------------------------------------------

    @property
    def daemon_version(self) -> str:
        """Return the cached daemon version string."""
        return self._daemon_version

    @property
    def port(self) -> int:
        """Return the daemon listen port."""
        return self._port

    @property
    def start_time(self) -> float:
        """Return the monotonic timestamp when the daemon started."""
        return self._start_time

    # -- Public methods ------------------------------------------------------

    def minimal_payload(self) -> dict[str, object]:
        """Return the public health payload safe for unauthenticated callers.

        Carries only liveness verdicts and service metrics. The TTS ``provider``
        is dropped (D1): naming the backend to an unauthenticated probe
        fingerprints it, and it is an out-of-jail host fact with no relative
        form. Everything else is added only by :meth:`full_payload`, behind the
        auth token.
        """
        uptime = time.monotonic() - self._start_time
        return {
            "status": "ok",
            "uptime_seconds": round(uptime, 1),
            "queued": self._playback.queue_size,
            "port": self._port,
            "active_sessions": self._get_client_count(),
        }

    def full_payload(self) -> dict[str, object]:
        """Return authenticated daemon diagnostics without host paths.

        Provider readiness is exposed separately through provider_status. Playback
        paths are relativized. The pid supports service lifecycle operations;
        daemon_version supports installation checks.
        """
        payload = self.minimal_payload()
        result = self._playback.last_result
        payload.update(
            last_playback=result.to_health_dict() if result else None,
            pid=os.getpid(),
            daemon_version=self._daemon_version,
            log_level=daemon_log_level(),
        )
        return payload

    def set_daemon_version(self, val: str) -> None:
        """Override the cached daemon version. For test use."""
        self._daemon_version = val

    def set_bound_port(self, port: int) -> None:
        """Record the port assigned to the listening socket."""
        self._port = port
