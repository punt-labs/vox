"""Tests for punt_vox.voxd.music.off_handler."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from starlette.websockets import WebSocket

from punt_vox.voxd.music.off_handler import MusicOffHandler
from punt_vox.voxd.music.types import MusicResponse

__all__: list[str] = []


def _make_ws() -> MagicMock:
    """Return a mock WebSocket with an async send_json."""
    ws: MagicMock = MagicMock(spec=WebSocket)
    ws.send_json = AsyncMock()
    return ws


class TestMusicOffHandler:
    """MusicOffHandler delegates to scheduler.turn_off."""

    def test_delegates_to_scheduler(self) -> None:
        scheduler = MagicMock()
        scheduler.turn_off = AsyncMock(return_value=MusicResponse(status="stopped"))
        handler = MusicOffHandler(scheduler=scheduler)
        ws = _make_ws()
        msg: dict[str, object] = {"id": "off-1"}

        asyncio.run(handler(msg, ws))

        scheduler.turn_off.assert_called_once()
        resp = ws.send_json.call_args[0][0]
        assert resp["type"] == "music_off"
        assert resp["status"] == "stopped"
        assert resp["id"] == "off-1"
