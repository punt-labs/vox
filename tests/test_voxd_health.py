"""Tests for punt_vox.voxd.health -- DaemonHealth class."""
# pyright: reportPrivateUsage=false

from __future__ import annotations

import os
from typing import TYPE_CHECKING, cast

from punt_vox.voxd import DaemonHealth, PlaybackQueue

if TYPE_CHECKING:
    import pytest


def _make_health(*, port: int = 0, client_count: int = 0) -> DaemonHealth:
    """Build a DaemonHealth with a fresh PlaybackQueue and fixed client count."""
    playback = PlaybackQueue()
    return DaemonHealth(playback, lambda: client_count, port)


class TestDaemonHealthConstruction:
    """DaemonHealth is created with correct defaults."""

    def test_daemon_version_from_installed_package(self) -> None:
        import importlib.metadata

        health = _make_health()

        try:
            expected = importlib.metadata.version("punt-vox")
        except importlib.metadata.PackageNotFoundError:
            from punt_vox import __version__

            expected = __version__
        assert health.daemon_version == expected

    def test_port_property(self) -> None:
        health = _make_health(port=9999)
        assert health.port == 9999

    def test_start_time_is_monotonic(self) -> None:
        import time

        before = time.monotonic()
        health = _make_health()
        after = time.monotonic()
        assert before <= health.start_time <= after

    def test_set_daemon_version(self) -> None:
        health = _make_health()
        health.set_daemon_version("99.99.99-test-sentinel")
        assert health.daemon_version == "99.99.99-test-sentinel"


class TestHealthPayloadFull:
    """The authenticated WS health payload exposes audio state for vox doctor."""

    def test_includes_audio_env_and_player_binary(self) -> None:
        health = _make_health()
        payload = health.full_payload()

        assert "audio_env" in payload
        assert "player_binary" in payload
        assert "last_playback" in payload
        audio_env = cast("dict[str, str]", payload["audio_env"])
        assert "XDG_RUNTIME_DIR" in audio_env
        assert "PULSE_SERVER" in audio_env
        assert "DBUS_SESSION_BUS_ADDRESS" in audio_env

    def test_includes_daemon_version_matching_installed_package(self) -> None:
        """Authenticated payload carries daemon_version from importlib.metadata."""
        import importlib.metadata

        health = _make_health()
        payload = health.full_payload()

        assert "daemon_version" in payload
        try:
            expected = importlib.metadata.version("punt-vox")
        except importlib.metadata.PackageNotFoundError:
            from punt_vox import __version__

            expected = __version__
        assert payload["daemon_version"] == expected

    def test_daemon_version_cached_not_per_request(self) -> None:
        """DaemonHealth caches the version once -- not per request."""
        health = _make_health()
        health.set_daemon_version("99.99.99-test-sentinel")
        payload = health.full_payload()
        assert payload["daemon_version"] == "99.99.99-test-sentinel"

    def test_includes_pid(self) -> None:
        """Authenticated payload includes os.getpid() so restart can verify."""
        health = _make_health()
        payload = health.full_payload()
        assert "pid" in payload
        assert payload["pid"] == os.getpid()

    def test_unset_audio_env_uses_sentinel(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for key in ("XDG_RUNTIME_DIR", "PULSE_SERVER", "DBUS_SESSION_BUS_ADDRESS"):
            monkeypatch.delenv(key, raising=False)
        health = _make_health()
        payload = health.full_payload()
        audio_env = cast("dict[str, str]", payload["audio_env"])
        assert audio_env["XDG_RUNTIME_DIR"] == "<unset>"
        assert audio_env["PULSE_SERVER"] == "<unset>"
        assert audio_env["DBUS_SESSION_BUS_ADDRESS"] == "<unset>"

    def test_last_playback_reflects_state(self) -> None:
        playback = PlaybackQueue()
        health = DaemonHealth(playback, lambda: 0, 0)
        playback.set_last_result(
            {"file": "/tmp/x.mp3", "rc": 0, "elapsed_s": 1.23, "stderr": "", "ts": 0.0}
        )
        payload = health.full_payload()
        assert payload["last_playback"] == playback.last_result


class TestHealthPayloadMinimal:
    """Unauthenticated HTTP /health must not leak sensitive diagnostic state."""

    def test_excludes_audio_env_and_last_playback(self) -> None:
        health = _make_health()
        payload = health.minimal_payload()

        assert "audio_env" not in payload
        assert "player_binary" not in payload
        assert "last_playback" not in payload
        assert payload["status"] == "ok"
        assert "uptime_seconds" in payload
        assert "queued" in payload

    def test_excludes_daemon_version_and_pid(self) -> None:
        """Public /health must not fingerprint the running version or pid."""
        health = _make_health()
        payload = health.minimal_payload()

        assert "daemon_version" not in payload
        assert "pid" not in payload

    def test_active_sessions_reads_callable(self) -> None:
        """Minimal payload reads client_count via the callable."""
        playback = PlaybackQueue()
        count = 7
        health = DaemonHealth(playback, lambda: count, 8421)
        payload = health.minimal_payload()
        assert payload["active_sessions"] == 7
        assert payload["port"] == 8421

    def test_http_health_route_excludes_daemon_version(self) -> None:
        """The HTTP /health response body must not carry daemon_version."""
        import json

        from starlette.testclient import TestClient

        from punt_vox.voxd import build_app

        health = _make_health()
        health.set_daemon_version("1.2.3-fingerprint-sentinel")
        app = build_app(health=health)

        with TestClient(app) as client:
            response = client.get("/health")

        body = json.loads(response.content)
        assert "daemon_version" not in body

    def test_http_health_route_returns_minimal_payload(self) -> None:
        import json

        from starlette.testclient import TestClient

        from punt_vox.voxd import build_app

        playback = PlaybackQueue()
        playback.set_last_result(
            {
                "file": "/tmp/x.mp3",
                "rc": 0,
                "elapsed_s": 0.5,
                "stderr": "secret stderr",
                "ts": 0.0,
            }
        )
        health = DaemonHealth(playback, lambda: 0, 0)
        app = build_app(playback=playback, health=health)

        with TestClient(app) as client:
            response = client.get("/health")

        body = json.loads(response.content)

        assert "audio_env" not in body
        assert "last_playback" not in body
        assert "secret stderr" not in response.text
