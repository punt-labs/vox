"""Tests for punt_vox.voxd.system_routes -- the subsystem-free route slice."""

from __future__ import annotations

from punt_vox.voxd.health import DaemonHealth
from punt_vox.voxd.mic_probe import MicProbeHandler
from punt_vox.voxd.playback import PlaybackQueue
from punt_vox.voxd.system_handlers import ChimeHandler, HealthHandler, VoicesHandler
from punt_vox.voxd.system_routes import SystemRoutes


def test_build_returns_the_four_system_routes() -> None:
    playback = PlaybackQueue()
    routes = SystemRoutes.build(
        playback=playback, health=DaemonHealth(playback, lambda: 0, 0)
    )

    assert set(routes) == {"chime", "voices", "health", "mic_probe"}
    assert isinstance(routes["chime"], ChimeHandler)
    assert isinstance(routes["voices"], VoicesHandler)
    assert isinstance(routes["health"], HealthHandler)
    assert isinstance(routes["mic_probe"], MicProbeHandler)
