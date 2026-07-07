"""Tests for punt_vox.voxd.router -- WebSocket message routing."""
# pyright: reportPrivateUsage=false

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast, final
from unittest.mock import MagicMock

import pytest

from punt_vox.voxd.chimes import ChimeResolver
from punt_vox.voxd.dedup import ChimeDedup, OnceDedup
from punt_vox.voxd.health import DaemonHealth
from punt_vox.voxd.playback import PlaybackQueue
from punt_vox.voxd.programs.wiring import ProgramSubsystem
from punt_vox.voxd.router import WebSocketRouter
from punt_vox.voxd.speech_handlers import RecordHandler, SynthesizeHandler
from punt_vox.voxd.synthesis import SynthesisPipeline
from punt_vox.voxd.system_handlers import ChimeHandler, HealthHandler, VoicesHandler
from punt_vox.voxd.types import MessageHandler

if TYPE_CHECKING:
    from pathlib import Path

    from punt_vox.voxd.programs.part import Part
    from punt_vox.voxd.programs.producer import PartSpec


@final
class _UnusedProducer:
    """A producer the router tests never invoke (they exercise no generation)."""

    __slots__ = ()

    async def produce(self, spec: PartSpec, target: Path) -> Part:
        """Never called by router tests -- routing does not generate audio."""
        raise NotImplementedError


def _make_router(
    *,
    auth_token: str | None = None,
) -> WebSocketRouter:
    """Build a WebSocketRouter for testing without touching real files."""
    from punt_vox.dirs import default_output_dir

    pb = PlaybackQueue()
    hl = DaemonHealth(pb, lambda: 0, 0)
    syn = SynthesisPipeline(playback_mutex=pb.mutex)
    programs = ProgramSubsystem(default_output_dir() / "programs", _UnusedProducer())

    handlers: dict[str, MessageHandler] = {
        "synthesize": SynthesizeHandler(
            synthesis=syn,
            playback=pb,
            once_dedup=OnceDedup(),
        ),
        "record": RecordHandler(synthesis=syn),
        "chime": ChimeHandler(
            chimes=ChimeResolver(),
            chime_dedup=ChimeDedup(),
            playback=pb,
        ),
        "voices": VoicesHandler(),
        "health": HealthHandler(health=hl),
        **programs.handlers(),
    }
    return WebSocketRouter(
        handlers=handlers,
        auth_token=auth_token,
    )


class TestWsRoutePeerClose:
    """handle_connection must exit quietly when the peer closes mid-receive."""

    @pytest.mark.asyncio
    async def test_state_check_preempts_disconnected_receive(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """State check preempts receive_text on a peer-closed socket."""
        from starlette.websockets import WebSocketState

        router = _make_router()

        async def _accept() -> None:
            return None

        async def _close(code: int = 1000) -> None:
            return None

        receive_calls = 0

        async def _receive_text() -> str:
            nonlocal receive_calls
            receive_calls += 1
            raise AssertionError(
                "receive_text must not be called once state is DISCONNECTED"
            )

        fake_ws = MagicMock()
        fake_ws.headers = {}
        fake_ws.query_params = {}
        fake_ws.accept = _accept
        fake_ws.close = _close
        fake_ws.receive_text = _receive_text
        fake_ws.application_state = WebSocketState.DISCONNECTED

        with caplog.at_level(logging.ERROR, logger="punt_vox.voxd"):
            await router.handle_connection(cast("object", fake_ws))  # type: ignore[arg-type]

        ws_error_records = [
            rec
            for rec in caplog.records
            if rec.levelno == logging.ERROR
            and rec.name == "punt_vox.voxd.router"
            and "WebSocket error" in rec.getMessage()
        ]
        assert ws_error_records == []
        assert receive_calls == 0
        assert router.client_count == 0

    @pytest.mark.asyncio
    async def test_logs_error_when_receive_raises_unexpected_runtimeerror(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Unexpected RuntimeError from receive_text still logs an error."""
        from starlette.websockets import WebSocketState

        router = _make_router()

        async def _accept() -> None:
            return None

        async def _close(code: int = 1000) -> None:
            return None

        async def _receive_text() -> str:
            raise RuntimeError("something else entirely")

        fake_ws = MagicMock()
        fake_ws.headers = {}
        fake_ws.query_params = {}
        fake_ws.accept = _accept
        fake_ws.close = _close
        fake_ws.receive_text = _receive_text
        fake_ws.application_state = WebSocketState.CONNECTED

        with caplog.at_level(logging.ERROR, logger="punt_vox.voxd"):
            await router.handle_connection(cast("object", fake_ws))  # type: ignore[arg-type]

        ws_error_records = [
            rec
            for rec in caplog.records
            if rec.levelno == logging.ERROR
            and rec.name == "punt_vox.voxd.router"
            and "WebSocket error" in rec.getMessage()
        ]
        assert len(ws_error_records) == 1
        assert router.client_count == 0


class TestHandlerRegistration:
    """All handlers are registered in the router."""

    def test_all_expected_handlers_registered(self) -> None:
        router = _make_router()
        expected = {
            "synthesize",
            "chime",
            "record",
            "voices",
            "health",
            "program_on",
            "program_off",
            "program_next",
            "program_play",
            "program_loop",
            "program_list",
            "program_status",
        }
        assert set(router.handlers.keys()) == expected
