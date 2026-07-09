"""Tests for the ``program_select`` wire handler -- id vs. tag-query routing (F#7)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, cast, final

from punt_vox.voxd.programs.select_handler import SelectHandler

from .conftest import make_service, seed_album

if TYPE_CHECKING:
    from pathlib import Path

    from starlette.websockets import WebSocket

    from punt_vox.voxd.programs.service import ProgramService


@final
class FakeWebSocket:
    """Record every ``send_json`` payload the handler emits."""

    __slots__ = ("sent",)
    sent: list[dict[str, object]]

    def __new__(cls) -> Self:
        self = super().__new__(cls)
        self.sent = []
        return self

    async def send_json(self, payload: dict[str, object]) -> None:
        self.sent.append(payload)


async def _reply(service: ProgramService, msg: dict[str, object]) -> dict[str, object]:
    ws = FakeWebSocket()
    await SelectHandler(service)(msg, cast("WebSocket", ws))
    return ws.sent[0]


class TestSelectHandler:
    async def test_by_tags_resolves_and_plays(self, tmp_path: Path) -> None:
        seed_album(tmp_path / "programs", 1, 2, style="trance", vibe="calm")
        service = make_service(tmp_path / "programs")
        reply = await _reply(service, {"id": "1", "style": "trance", "vibe": "calm"})
        assert reply == {"type": "program_select", "id": "1"}
        await service.run_once()
        assert service.status().now_playing is not None

    async def test_by_id_is_a_direct_lookup(self, tmp_path: Path) -> None:
        seed_album(
            tmp_path / "programs", 1, style="trance", vibe="calm", album_id="a3f1c9"
        )
        service = make_service(tmp_path / "programs")
        reply = await _reply(service, {"id": "2", "album_id": "a3f1c9"})
        # A present 'album_id' routes to replay_album (direct lookup, not a query).
        assert reply["type"] == "program_select"
        await service.run_once()
        assert service.status().now_playing is not None

    async def test_unknown_id_is_a_boundary_error(self, tmp_path: Path) -> None:
        service = make_service(tmp_path / "programs")
        reply = await _reply(service, {"id": "req", "album_id": "badbad"})
        assert reply["type"] == "error"
        assert "no album with id" in str(reply["message"])

    async def test_no_match_is_a_boundary_error(self, tmp_path: Path) -> None:
        service = make_service(tmp_path / "programs")
        reply = await _reply(service, {"id": "req", "style": "ghost"})
        assert reply["type"] == "error"
        assert "no albums match" in str(reply["message"])
