"""Tests for punt_vox.voxd.music.next_handler."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from starlette.websockets import WebSocket

from punt_vox.voxd.music.next_handler import MusicNextHandler
from punt_vox.voxd.music.types import MusicResponse

__all__: list[str] = []


def _make_ws() -> MagicMock:
    """Return a mock WebSocket with an async send_json."""
    ws: MagicMock = MagicMock(spec=WebSocket)
    ws.send_json = AsyncMock()
    return ws


class TestMusicNextHandler:
    """MusicNextHandler delegates to scheduler.skip_next."""

    def test_delegates_to_scheduler(self) -> None:
        scheduler = MagicMock()
        scheduler.skip_next.return_value = MusicResponse(status="generating")
        handler = MusicNextHandler(scheduler=scheduler)
        ws = _make_ws()
        msg: dict[str, object] = {
            "id": "next-1",
            "owner_id": "sess-abc",
        }

        asyncio.run(handler(msg, ws))

        scheduler.skip_next.assert_called_once_with("sess-abc")
        resp = ws.send_json.call_args[0][0]
        assert resp["type"] == "music_next"
        assert resp["status"] == "generating"

    def test_ignored_when_off(self) -> None:
        scheduler = MagicMock()
        scheduler.skip_next.return_value = MusicResponse(status="ignored")
        handler = MusicNextHandler(scheduler=scheduler)
        ws = _make_ws()
        msg: dict[str, object] = {
            "id": "next-2",
            "owner_id": "sess-abc",
        }

        asyncio.run(handler(msg, ws))

        resp = ws.send_json.call_args[0][0]
        assert resp["status"] == "ignored"

    def test_catches_value_error(self) -> None:
        scheduler = MagicMock()
        scheduler.skip_next.side_effect = ValueError("owner_id is required")
        handler = MusicNextHandler(scheduler=scheduler)
        ws = _make_ws()
        msg: dict[str, object] = {"id": "next-3", "owner_id": ""}

        asyncio.run(handler(msg, ws))

        resp = ws.send_json.call_args[0][0]
        assert resp["type"] == "error"
        assert "owner_id is required" in resp["message"]
