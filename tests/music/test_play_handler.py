"""Tests for punt_vox.voxd.music.play_handler."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from starlette.websockets import WebSocket

from punt_vox.voxd.music.play_handler import MusicPlayHandler
from punt_vox.voxd.music.types import MusicResponse

__all__: list[str] = []


def _make_ws() -> MagicMock:
    """Return a mock WebSocket with an async send_json."""
    ws: MagicMock = MagicMock(spec=WebSocket)
    ws.send_json = AsyncMock()
    return ws


class TestMusicPlayHandler:
    """MusicPlayHandler delegates to scheduler.play_track."""

    def test_delegates_to_scheduler(self) -> None:
        scheduler = MagicMock()
        scheduler.play_track = AsyncMock(
            return_value=MusicResponse(
                status="playing", track="/music/chill.mp3", name="chill"
            )
        )
        handler = MusicPlayHandler(scheduler=scheduler)
        ws = _make_ws()
        msg: dict[str, object] = {
            "id": "play-1",
            "name": "chill vibes",
            "owner_id": "sess-a",
        }

        asyncio.run(handler(msg, ws))

        scheduler.play_track.assert_called_once_with("chill vibes", "sess-a")
        resp = ws.send_json.call_args[0][0]
        assert resp["type"] == "music_play"
        assert resp["status"] == "playing"
        assert resp["track"] == "/music/chill.mp3"
        assert resp["name"] == "chill"

    def test_catches_value_error_missing_name(self) -> None:
        scheduler = MagicMock()
        scheduler.play_track = AsyncMock(side_effect=ValueError("name is required"))
        handler = MusicPlayHandler(scheduler=scheduler)
        ws = _make_ws()
        msg: dict[str, object] = {"id": "play-2", "name": "", "owner_id": "a"}

        asyncio.run(handler(msg, ws))

        resp = ws.send_json.call_args[0][0]
        assert resp["type"] == "error"
        assert "name is required" in resp["message"]

    def test_catches_value_error_not_found(self) -> None:
        scheduler = MagicMock()
        scheduler.play_track = AsyncMock(
            side_effect=ValueError("track not found: nope")
        )
        handler = MusicPlayHandler(scheduler=scheduler)
        ws = _make_ws()
        msg: dict[str, object] = {
            "id": "play-3",
            "name": "nope",
            "owner_id": "b",
        }

        asyncio.run(handler(msg, ws))

        resp = ws.send_json.call_args[0][0]
        assert resp["type"] == "error"
        assert "not found" in resp["message"]
