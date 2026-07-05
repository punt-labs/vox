"""Tests for punt_vox.voxd.music.on_handler."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from starlette.websockets import WebSocket

from punt_vox.voxd.music.on_handler import MusicOnHandler
from punt_vox.voxd.music.pool import POOL_SIZE
from punt_vox.voxd.music.prompts import PromptSet
from punt_vox.voxd.music.types import MusicResponse

__all__: list[str] = []


def _variations() -> list[str]:
    """Return POOL_SIZE distinct variation strings."""
    return [f"var{i}" for i in range(POOL_SIZE)]


def _make_ws() -> MagicMock:
    """Return a mock WebSocket with an async send_json."""
    ws: MagicMock = MagicMock(spec=WebSocket)
    ws.send_json = AsyncMock()
    return ws


class TestMusicOnHandler:
    """MusicOnHandler delegates to scheduler.turn_on."""

    def test_delegates_to_scheduler(self) -> None:
        scheduler = MagicMock()
        scheduler.turn_on = AsyncMock(return_value=MusicResponse(status="generating"))
        handler = MusicOnHandler(scheduler=scheduler)
        ws = _make_ws()
        msg: dict[str, object] = {
            "id": "1",
            "owner_id": "abc",
            "style": "techno",
            "vibe": "focused",
            "vibe_tags": "[calm]",
            "name": "",
        }

        asyncio.run(handler(msg, ws))

        scheduler.turn_on.assert_called_once_with(
            "abc", "techno", ("focused", "[calm]"), "", prompts=None
        )
        ws.send_json.assert_called_once()
        resp = ws.send_json.call_args[0][0]
        assert resp["type"] == "music_on"
        assert resp["status"] == "generating"

    def test_includes_track_and_name_on_replay(self) -> None:
        scheduler = MagicMock()
        scheduler.turn_on = AsyncMock(
            return_value=MusicResponse(
                status="playing", track="/music/my_focus.mp3", name="my_focus"
            )
        )
        handler = MusicOnHandler(scheduler=scheduler)
        ws = _make_ws()
        msg: dict[str, object] = {
            "id": "2",
            "owner_id": "abc",
            "style": "",
            "vibe": "",
            "vibe_tags": "",
            "name": "my focus",
        }

        asyncio.run(handler(msg, ws))

        resp = ws.send_json.call_args[0][0]
        assert resp["status"] == "playing"
        assert resp["track"] == "/music/my_focus.mp3"
        assert resp["name"] == "my_focus"

    def test_catches_value_error(self) -> None:
        scheduler = MagicMock()
        scheduler.turn_on = AsyncMock(side_effect=ValueError("owner_id is required"))
        handler = MusicOnHandler(scheduler=scheduler)
        ws = _make_ws()
        msg: dict[str, object] = {
            "id": "3",
            "owner_id": "",
            "style": "",
            "vibe": "",
            "vibe_tags": "",
            "name": "",
        }

        asyncio.run(handler(msg, ws))

        resp = ws.send_json.call_args[0][0]
        assert resp["type"] == "error"
        assert "owner_id is required" in resp["message"]

    def test_forwards_agent_prompts(self) -> None:
        scheduler = MagicMock()
        scheduler.turn_on = AsyncMock(return_value=MusicResponse(status="generating"))
        handler = MusicOnHandler(scheduler=scheduler)
        ws = _make_ws()
        msg: dict[str, object] = {
            "id": "5",
            "owner_id": "abc",
            "style": "klezmer",
            "vibe": "celebratory",
            "vibe_tags": "",
            "name": "",
            "base_prompt": "Klezmer, clarinet lead",
            "variations": _variations(),
        }

        asyncio.run(handler(msg, ws))

        prompts = scheduler.turn_on.call_args.kwargs["prompts"]
        assert prompts == PromptSet.from_agent("Klezmer, clarinet lead", _variations())

    def test_reports_invalid_prompt_shape(self) -> None:
        scheduler = MagicMock()
        scheduler.turn_on = AsyncMock(return_value=MusicResponse(status="generating"))
        handler = MusicOnHandler(scheduler=scheduler)
        ws = _make_ws()
        msg: dict[str, object] = {
            "id": "6",
            "owner_id": "abc",
            "style": "klezmer",
            "vibe": "",
            "vibe_tags": "",
            "name": "",
            "base_prompt": "Klezmer",
            "variations": _variations()[:-1],  # 11 -- wrong count
        }

        asyncio.run(handler(msg, ws))

        scheduler.turn_on.assert_not_called()
        resp = ws.send_json.call_args[0][0]
        assert resp["type"] == "error"
        assert f"exactly {POOL_SIZE}" in resp["message"]
