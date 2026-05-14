"""Tests for punt_vox.voxd.router -- WebSocket message routing."""
# pyright: reportPrivateUsage=false

from __future__ import annotations

import logging
from typing import cast
from unittest.mock import MagicMock

import pytest

from punt_vox.voxd.chimes import ChimeResolver
from punt_vox.voxd.dedup import ChimeDedup, OnceDedup
from punt_vox.voxd.health import DaemonHealth
from punt_vox.voxd.music.generator import TrackGenerator
from punt_vox.voxd.music.scheduler import MusicScheduler
from punt_vox.voxd.music_handlers import (
    MusicListHandler,
    MusicNextHandler,
    MusicOffHandler,
    MusicOnHandler,
    MusicPlayHandler,
    MusicVibeHandler,
)
from punt_vox.voxd.playback import PlaybackQueue
from punt_vox.voxd.router import WebSocketRouter
from punt_vox.voxd.speech_handlers import RecordHandler, SynthesizeHandler
from punt_vox.voxd.synthesis import SynthesisPipeline
from punt_vox.voxd.system_handlers import ChimeHandler, HealthHandler, VoicesHandler
from punt_vox.voxd.types import MessageHandler


def _make_router(
    *,
    auth_token: str | None = None,
) -> WebSocketRouter:
    """Build a WebSocketRouter for testing without touching real files."""
    from punt_vox.dirs import music_output_dir

    pb = PlaybackQueue()
    tg = TrackGenerator(music_output_dir())
    ms = MusicScheduler(tg)
    hl = DaemonHealth(pb, lambda: 0, 0)
    syn = SynthesisPipeline(playback_mutex=pb.mutex)

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
        "music_on": MusicOnHandler(music=ms, track_generator=tg),
        "music_off": MusicOffHandler(music=ms),
        "music_play": MusicPlayHandler(music=ms, track_generator=tg),
        "music_list": MusicListHandler(track_generator=tg),
        "music_vibe": MusicVibeHandler(music=ms),
        "music_next": MusicNextHandler(music=ms),
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
            "music_on",
            "music_off",
            "music_play",
            "music_list",
            "music_vibe",
            "music_next",
        }
        assert set(router.handlers.keys()) == expected
