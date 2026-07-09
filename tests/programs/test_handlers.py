"""Tests for the program wire handlers -- thin adapters over the service.

Each handler is driven with a fake WebSocket (records ``send_json``) and a real
filesystem-backed :class:`ProgramService`; posted commands are applied via
``run_once`` so the reply shape *and* the resulting daemon state are asserted.
The handlers never mutate the source -- they POST a signal the sole writer
applies -- so a rejected or unresolvable command surfaces as a boundary error.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, cast, final

from punt_vox.voxd.programs.list_handler import ListHandler
from punt_vox.voxd.programs.next_handler import NextHandler
from punt_vox.voxd.programs.off_handler import OffHandler
from punt_vox.voxd.programs.on_handler import OnHandler
from punt_vox.voxd.programs.select_handler import SelectHandler
from punt_vox.voxd.programs.status_handler import StatusHandler

from .conftest import make_service, seed_album

if TYPE_CHECKING:
    from pathlib import Path

    from starlette.websockets import WebSocket

    from punt_vox.voxd.programs.service import ProgramService
    from punt_vox.voxd.types import MessageHandler


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


def _service(tmp_path: Path) -> ProgramService:
    return make_service(tmp_path / "programs")


async def _reply(handler: MessageHandler, msg: dict[str, object]) -> dict[str, object]:
    """Invoke ``handler`` with ``msg`` and return the single reply it sent."""
    ws = FakeWebSocket()
    await handler(msg, cast("WebSocket", ws))
    return ws.sent[0]


class TestStatusHandler:
    async def test_idle_status(self, tmp_path: Path) -> None:
        reply = await _reply(StatusHandler(_service(tmp_path)), {"id": "1"})
        assert reply["type"] == "program_status"
        assert reply["id"] == "1"
        assert isinstance(reply["status"], dict)
        assert reply["status"]["mode"] == "off"

    async def test_status_reflects_a_replayed_selection(self, tmp_path: Path) -> None:
        seed_album(tmp_path / "programs", 1, 2, style="trance", vibe="calm")
        service = _service(tmp_path)
        await _reply(SelectHandler(service), {"id": "p", "style": "trance"})
        await service.run_once()
        reply = await _reply(StatusHandler(service), {"id": "s"})
        status = reply["status"]
        assert isinstance(status, dict)
        assert status["now_playing"] is not None


class TestMutatingHandlers:
    async def test_on_acks_and_activates(self, tmp_path: Path) -> None:
        service = _service(tmp_path)
        reply = await _reply(
            OnHandler(service), {"id": "1", "style": "t", "vibe": "calm", "name": "mix"}
        )
        assert reply == {"type": "program_on", "id": "1"}
        await service.run_once()
        service.shutdown()
        assert not service.status().is_idle

    async def test_off_acks(self, tmp_path: Path) -> None:
        reply = await _reply(OffHandler(_service(tmp_path)), {"id": "9"})
        assert reply == {"type": "program_off", "id": "9"}

    async def test_next_acks(self, tmp_path: Path) -> None:
        reply = await _reply(NextHandler(_service(tmp_path)), {"id": "2"})
        assert reply == {"type": "program_next", "id": "2"}

    async def test_select_by_tags_acks_and_plays(self, tmp_path: Path) -> None:
        seed_album(tmp_path / "programs", 1, 2, 3, style="trance", vibe="calm")
        service = _service(tmp_path)
        reply = await _reply(
            SelectHandler(service), {"id": "3", "style": "trance", "vibe": "calm"}
        )
        assert reply == {"type": "program_select", "id": "3"}
        await service.run_once()
        assert service.status().now_playing is not None

    async def test_select_no_match_is_an_error(self, tmp_path: Path) -> None:
        reply = await _reply(
            SelectHandler(_service(tmp_path)), {"id": "3", "style": "ghost"}
        )
        assert reply["type"] == "error"
        assert "no albums match" in str(reply["message"])

    async def test_select_bad_id_is_an_error(self, tmp_path: Path) -> None:
        reply = await _reply(
            SelectHandler(_service(tmp_path)), {"id": "3", "id_arg": "x"}
        )
        # No 'id' field -> tag query over nothing -> no match error.
        assert reply["type"] == "error"


class TestListHandler:
    async def test_lists_albums_with_tags_and_counts(self, tmp_path: Path) -> None:
        root = tmp_path / "programs"
        seed_album(root, 1, 2, style="trance", vibe="calm", album_id="a3f1c9")
        seed_album(root, 1, style="lofi", vibe="focus", album_id="7b2e04")
        reply = await _reply(ListHandler(_service(tmp_path)), {"id": "5"})
        assert reply["type"] == "program_list"
        programs = reply["programs"]
        assert isinstance(programs, list)
        assert {p["id"] for p in programs} == {"a3f1c9", "7b2e04"}
        trance = next(p for p in programs if p["id"] == "a3f1c9")
        assert trance["style"] == "trance"
        assert trance["vibe"] == "calm"
        assert trance["ready"] == 2

    async def test_empty_catalogue(self, tmp_path: Path) -> None:
        reply = await _reply(ListHandler(_service(tmp_path)), {"id": "6"})
        assert reply["programs"] == []
