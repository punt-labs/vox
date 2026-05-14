"""Tests for punt_vox.voxd.music.vibe_handler."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from starlette.websockets import WebSocket

from punt_vox.voxd.music.types import MusicResponse
from punt_vox.voxd.music.vibe_handler import MusicVibeHandler

__all__: list[str] = []


def _make_ws() -> MagicMock:
    """Return a mock WebSocket with an async send_json."""
    ws: MagicMock = MagicMock(spec=WebSocket)
    ws.send_json = AsyncMock()
    return ws


class TestMusicVibeHandler:
    """MusicVibeHandler delegates to scheduler.update_vibe."""

    def test_delegates_to_scheduler(self) -> None:
        scheduler = MagicMock()
        scheduler.update_vibe.return_value = MusicResponse(status="generating")
        handler = MusicVibeHandler(scheduler=scheduler)
        ws = _make_ws()
        msg: dict[str, object] = {
            "id": "vibe-1",
            "owner_id": "sess-abc",
            "vibe": "happy",
            "vibe_tags": "[warm]",
        }

        asyncio.run(handler(msg, ws))

        scheduler.update_vibe.assert_called_once_with("sess-abc", ("happy", "[warm]"))
        resp = ws.send_json.call_args[0][0]
        assert resp["type"] == "music_vibe"
        assert resp["status"] == "generating"

    def test_ignored_status(self) -> None:
        scheduler = MagicMock()
        scheduler.update_vibe.return_value = MusicResponse(status="ignored")
        handler = MusicVibeHandler(scheduler=scheduler)
        ws = _make_ws()
        msg: dict[str, object] = {
            "id": "vibe-2",
            "owner_id": "other",
            "vibe": "happy",
            "vibe_tags": "",
        }

        asyncio.run(handler(msg, ws))

        resp = ws.send_json.call_args[0][0]
        assert resp["status"] == "ignored"

    def test_catches_value_error(self) -> None:
        scheduler = MagicMock()
        scheduler.update_vibe.side_effect = ValueError("owner_id is required")
        handler = MusicVibeHandler(scheduler=scheduler)
        ws = _make_ws()
        msg: dict[str, object] = {
            "id": "vibe-3",
            "owner_id": "",
            "vibe": "happy",
            "vibe_tags": "",
        }

        asyncio.run(handler(msg, ws))

        resp = ws.send_json.call_args[0][0]
        assert resp["type"] == "error"
        assert "owner_id is required" in resp["message"]
