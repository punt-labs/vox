"""Tests for the seven program wire handlers -- thin adapters over the service.

Each handler is driven with a fake WebSocket (records ``send_json``) and a real
filesystem-backed :class:`ProgramService`; posted commands are applied via
``run_once`` so the reply shape *and* the resulting daemon state are asserted.
The handlers never mutate the Program -- they POST a signal the sole writer
applies -- so a rejected or out-of-range command surfaces as a boundary error.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, cast, final

from punt_vox.voxd.programs.list_handler import ListHandler
from punt_vox.voxd.programs.loop_handler import LoopHandler
from punt_vox.voxd.programs.next_handler import NextHandler
from punt_vox.voxd.programs.off_handler import OffHandler
from punt_vox.voxd.programs.on_handler import OnHandler
from punt_vox.voxd.programs.play_handler import PlayHandler
from punt_vox.voxd.programs.status_handler import StatusHandler

from .conftest import make_service, seed_program

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

    async def test_status_reflects_a_played_program(self, tmp_path: Path) -> None:
        service = _service(tmp_path)
        seed_program(tmp_path / "programs", "saved", 1, 2)
        await _reply(PlayHandler(service), {"id": "p", "name": "saved"})
        await service.run_once()
        reply = await _reply(StatusHandler(service), {"id": "s"})
        status = reply["status"]
        assert isinstance(status, dict)
        assert status["name"] == "saved"
        assert status["now_playing"] is not None


class TestMutatingHandlers:
    async def test_on_acks_and_activates(self, tmp_path: Path) -> None:
        service = _service(tmp_path)
        reply = await _reply(
            OnHandler(service), {"id": "1", "style": "t", "name": "mix"}
        )
        assert reply == {"type": "program_on", "id": "1"}
        await service.run_once()
        service.shutdown()
        assert not service.status().is_idle

    async def test_on_rejects_a_path_bearing_name(self, tmp_path: Path) -> None:
        reply = await _reply(OnHandler(_service(tmp_path)), {"id": "1", "name": "a/b"})
        assert reply["type"] == "error"
        assert "path separators" in str(reply["message"])

    async def test_off_acks(self, tmp_path: Path) -> None:
        reply = await _reply(OffHandler(_service(tmp_path)), {"id": "9"})
        assert reply == {"type": "program_off", "id": "9"}

    async def test_next_acks(self, tmp_path: Path) -> None:
        reply = await _reply(NextHandler(_service(tmp_path)), {"id": "2"})
        assert reply == {"type": "program_next", "id": "2"}

    async def test_play_acks_and_plays(self, tmp_path: Path) -> None:
        service = _service(tmp_path)
        seed_program(tmp_path / "programs", "saved", 1, 2, 3)
        reply = await _reply(
            PlayHandler(service), {"id": "3", "name": "saved", "part": 2}
        )
        assert reply == {"type": "program_play", "id": "3"}
        await service.run_once()
        now = service.status().now_playing
        assert now is not None
        assert now.index == 2

    async def test_play_unknown_is_an_error(self, tmp_path: Path) -> None:
        reply = await _reply(
            PlayHandler(_service(tmp_path)), {"id": "3", "name": "ghost"}
        )
        assert reply["type"] == "error"
        assert "no saved program" in str(reply["message"])

    async def test_play_out_of_range_part_is_an_error(self, tmp_path: Path) -> None:
        seed_program(tmp_path / "programs", "saved", 1, 2)
        reply = await _reply(
            PlayHandler(_service(tmp_path)), {"id": "3", "name": "saved", "part": 9}
        )
        assert reply["type"] == "error"
        assert "out of range" in str(reply["message"])

    async def test_play_missing_name_is_an_error(self, tmp_path: Path) -> None:
        reply = await _reply(PlayHandler(_service(tmp_path)), {"id": "3"})
        assert reply["type"] == "error"

    async def test_loop_acks(self, tmp_path: Path) -> None:
        service = _service(tmp_path)
        seed_program(tmp_path / "programs", "saved", 1, 2)
        reply = await _reply(LoopHandler(service), {"id": "4", "name": "saved"})
        assert reply == {"type": "program_loop", "id": "4"}


class TestListHandler:
    async def test_lists_saved_programs_with_counts(self, tmp_path: Path) -> None:
        seed_program(tmp_path / "programs", "alpha", 1, 2)
        seed_program(tmp_path / "programs", "beta", 1)
        reply = await _reply(ListHandler(_service(tmp_path)), {"id": "5"})
        assert reply["type"] == "program_list"
        programs = reply["programs"]
        assert isinstance(programs, list)
        assert {p["name"] for p in programs} == {"alpha", "beta"}
        alpha = next(p for p in programs if p["name"] == "alpha")
        assert alpha == {"name": "alpha", "format": "music", "ready": 2, "total": 2}

    async def test_empty_catalogue(self, tmp_path: Path) -> None:
        reply = await _reply(ListHandler(_service(tmp_path)), {"id": "6"})
        assert reply["programs"] == []
