"""Tests for punt_vox.voxd.music.list_handler."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from starlette.websockets import WebSocket

from punt_vox.voxd.music.generator import MusicTrack
from punt_vox.voxd.music.list_handler import MusicListHandler

__all__: list[str] = []


def _make_ws() -> MagicMock:
    """Return a mock WebSocket with an async send_json."""
    ws: MagicMock = MagicMock(spec=WebSocket)
    ws.send_json = AsyncMock()
    return ws


class TestMusicListHandler:
    """MusicListHandler delegates to generator.list_tracks."""

    def test_delegates_to_generator(self) -> None:
        generator = MagicMock()
        generator.list_tracks.return_value = [
            MusicTrack(
                name="alpha",
                path=Path("/m/alpha.mp3"),
                size_bytes=1024,
                modified=1.0,
            ),
        ]
        handler = MusicListHandler(generator=generator)
        ws = _make_ws()
        msg: dict[str, object] = {"id": "list-1"}

        asyncio.run(handler(msg, ws))

        generator.list_tracks.assert_called_once()
        resp = ws.send_json.call_args[0][0]
        assert resp["type"] == "music_list"
        assert resp["id"] == "list-1"
        assert len(resp["tracks"]) == 1
        assert resp["tracks"][0]["name"] == "alpha"

    def test_empty_list(self) -> None:
        generator = MagicMock()
        generator.list_tracks.return_value = []
        handler = MusicListHandler(generator=generator)
        ws = _make_ws()
        msg: dict[str, object] = {"id": "list-2"}

        asyncio.run(handler(msg, ws))

        resp = ws.send_json.call_args[0][0]
        assert resp["tracks"] == []
