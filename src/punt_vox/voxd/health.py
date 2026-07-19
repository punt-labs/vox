"""Daemon health reporting for voxd."""
# pyright: reportPrivateUsage=false
# Internal module within the voxd package -- cross-module private access is expected.

from __future__ import annotations

import os
import time
from collections.abc import Callable
from typing import Self

from punt_vox.paths import installed_version
from punt_vox.voxd.playback import _AUDIO_ENV_KEYS, PlaybackQueue, _player_binary_path


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

        Excludes ``audio_env``, ``player_binary``, and ``last_playback`` so the
        HTTP ``/health`` route can never leak environment variables or stderr
        contents to non-localhost listeners.
        """
        from punt_vox.providers import auto_detect_provider

        uptime = time.monotonic() - self._start_time
        return {
            "status": "ok",
            "uptime_seconds": round(uptime, 1),
            "queued": self._playback.queue_size,
            "port": self._port,
            "active_sessions": self._get_client_count(),
            "provider": auto_detect_provider(),
        }

    def full_payload(self) -> dict[str, object]:
        """Return the full diagnostic health payload for authenticated callers.

        Adds the audio environment snapshot, the resolved player binary, the
        last playback result, the running process id, and the cached daemon
        version. Used only by the WebSocket health handler, which is gated
        by the auth token.

        The ``pid`` field is used by ``vox daemon restart`` to confirm the
        daemon has come back up as a fresh process. The ``daemon_version``
        field is used by ``vox doctor`` to warn when the running daemon
        does not match the wheel installed on disk. Neither is
        exposed on the unauthenticated HTTP ``/health`` route -- version
        info is a fingerprinting aid for targeted exploitation, and the
        minimal payload stays minimal.
        """
        payload = self.minimal_payload()
        payload["audio_env"] = {
            k: os.environ.get(k, "<unset>") for k in _AUDIO_ENV_KEYS
        }
        payload["player_binary"] = _player_binary_path()
        if result := self._playback.last_result:
            payload["last_playback"] = result.to_health_dict()
        else:
            payload["last_playback"] = None
        payload["pid"] = os.getpid()
        payload["daemon_version"] = self._daemon_version
        return payload

    def set_daemon_version(self, val: str) -> None:
        """Override the cached daemon version. For test use."""
        self._daemon_version = val
