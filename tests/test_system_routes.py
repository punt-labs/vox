"""Tests for punt_vox.voxd.system_routes -- the subsystem-free route slice."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, cast, final

import pytest

from punt_vox.voxd.health import DaemonHealth
from punt_vox.voxd.mic_probe import MicProbeHandler
from punt_vox.voxd.playback import PlaybackQueue
from punt_vox.voxd.system_handlers import ChimeHandler, HealthHandler, VoicesHandler
from punt_vox.voxd.system_routes import SystemRoutes

if TYPE_CHECKING:
    from starlette.websockets import WebSocket

    from punt_vox.voxd.output_health import OutputHealth


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


@pytest.mark.asyncio
async def test_the_health_route_carries_the_output_health_probe() -> None:
    """A health request with output_dirs set answers with the output probe's fields."""

    class _OutputHealth:
        async def probe_within_budget(self) -> dict[str, object]:
            return {"output_dirs": {"music": "writable"}}

    @final
    class _CollectingWs:
        __slots__ = ("_sent",)
        _sent: list[dict[str, object]]

        def __new__(cls) -> Self:
            self = super().__new__(cls)
            self._sent = []
            return self

        @property
        def sent(self) -> list[dict[str, object]]:
            return self._sent

        async def send_json(self, data: dict[str, object]) -> None:
            self._sent.append(data)

    playback = PlaybackQueue()
    routes = SystemRoutes.build(
        playback=playback,
        health=DaemonHealth(playback, lambda: 0, 0),
        output_health=cast("OutputHealth", _OutputHealth()),
    )
    ws = _CollectingWs()
    await routes["health"](
        {"type": "health", "id": "h1", "output_dirs": True}, cast("WebSocket", ws)
    )

    (reply,) = ws.sent
    assert reply["type"] == "health"
    assert reply["id"] == "h1"
    assert reply["output_dirs"] == {"music": "writable"}
