"""Tests for punt_vox.voxd.router -- WebSocket message routing."""
# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from punt_vox.voxd import (
    PlaybackItem,
    WebSocketRouter,
)
from punt_vox.voxd.chimes import ChimeResolver
from punt_vox.voxd.dedup import ChimeDedup, OnceDedup
from punt_vox.voxd.health import DaemonHealth
from punt_vox.voxd.music_scheduler import MusicScheduler
from punt_vox.voxd.playback import PlaybackQueue
from punt_vox.voxd.synthesis import SynthesisPipeline
from punt_vox.voxd.track_generator import TrackGenerator


def _make_router(
    *,
    auth_token: str | None = None,
    playback: PlaybackQueue | None = None,
    music: MusicScheduler | None = None,
    health: DaemonHealth | None = None,
    track_generator: TrackGenerator | None = None,
    synthesis: SynthesisPipeline | None = None,
) -> WebSocketRouter:
    """Build a WebSocketRouter for testing without touching real files."""
    from punt_vox.dirs import music_output_dir

    pb = playback or PlaybackQueue()
    tg = track_generator or TrackGenerator(music_output_dir())
    ms = music or MusicScheduler(tg)
    hl = health or DaemonHealth(pb, lambda: 0, 0)
    syn = synthesis or SynthesisPipeline(playback_mutex=pb.mutex)
    return WebSocketRouter(
        synthesis=syn,
        playback=pb,
        music=ms,
        chime_dedup=ChimeDedup(),
        once_dedup=OnceDedup(),
        chimes=ChimeResolver(),
        health=hl,
        auth_token=auth_token,
        track_generator=tg,
    )


# Build a MusicScheduler + WebSocketRouter pair for tests that inspect music state.
def _make_ctx_and_router() -> tuple[MusicScheduler, WebSocketRouter]:
    """Build a MusicScheduler and router pair for tests that inspect music state."""
    from punt_vox.dirs import music_output_dir

    pb = PlaybackQueue()
    tg = TrackGenerator(music_output_dir())
    ms = MusicScheduler(tg)
    hl = DaemonHealth(pb, lambda: 0, 0)
    syn = SynthesisPipeline(playback_mutex=pb.mutex)
    router = WebSocketRouter(
        synthesis=syn,
        playback=pb,
        music=ms,
        chime_dedup=ChimeDedup(),
        once_dedup=OnceDedup(),
        chimes=ChimeResolver(),
        health=hl,
        auth_token=None,
        track_generator=tg,
    )
    return ms, router


class TestHandleSynthesizeShortCircuit:
    """Router._handle_synthesize skips try_direct_play for cloud providers."""

    def test_cloud_provider_skips_direct_play(self) -> None:
        mock_synth = MagicMock(spec=SynthesisPipeline)
        mock_synth.try_direct_play = AsyncMock(return_value=None)
        mock_synth.synthesize_to_file = AsyncMock(side_effect=RuntimeError("stop here"))
        router = _make_router(synthesis=mock_synth)
        websocket = MagicMock()
        websocket.send_json = AsyncMock()
        msg: dict[str, object] = {
            "id": "1",
            "text": "hello",
            "provider": "elevenlabs",
        }

        asyncio.run(router._handle_synthesize(msg, websocket))

        mock_synth.try_direct_play.assert_not_called()

    def test_local_provider_calls_direct_play(self) -> None:
        mock_synth = MagicMock(spec=SynthesisPipeline)
        mock_synth.try_direct_play = AsyncMock(return_value=0)
        router = _make_router(synthesis=mock_synth)
        websocket = MagicMock()
        websocket.send_json = AsyncMock()
        msg: dict[str, object] = {
            "id": "2",
            "text": "hello",
            "provider": "espeak",
        }

        asyncio.run(router._handle_synthesize(msg, websocket))

        mock_synth.try_direct_play.assert_called_once()
        call_kwargs = mock_synth.try_direct_play.call_args.kwargs
        assert call_kwargs["provider_name"] == "espeak"


class TestHandleSynthesizeOnceFlag:
    """Integration tests for _handle_synthesize with the once flag."""

    @staticmethod
    def _make_stubbed_router(
        monkeypatch: pytest.MonkeyPatch,
    ) -> tuple[WebSocketRouter, list[str]]:
        """Build a router with fake synthesis and instant playback."""
        synthesis_calls: list[str] = []

        async def fake_synthesize(*args: object, **_kwargs: object) -> Path:
            synthesis_calls.append(str(args[0]))
            return Path("/tmp/fake.mp3")

        mock_synth = MagicMock(spec=SynthesisPipeline)
        mock_synth.synthesize_to_file = fake_synthesize

        monkeypatch.setattr("punt_vox.voxd.router._LOCAL_PROVIDERS", set[str]())
        monkeypatch.setattr(
            "punt_vox.voxd.router.auto_detect_provider", lambda: "elevenlabs"
        )

        router = _make_router(synthesis=mock_synth)

        class _InstantPlaybackQueue:
            async def put(self, item: PlaybackItem) -> None:
                item.notify.set()

        router._playback._queue = _InstantPlaybackQueue()  # type: ignore[assignment]
        return router, synthesis_calls

    @pytest.mark.asyncio
    async def test_once_null_does_not_dedupe(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without once, identical requests both proceed (regression)."""
        router, synthesis_calls = self._make_stubbed_router(monkeypatch)
        ws = MagicMock()
        ws.send_json = AsyncMock()

        msg: dict[str, object] = {
            "type": "synthesize",
            "id": "a",
            "text": "hello",
        }
        await router._handle_synthesize(msg, ws)
        msg2: dict[str, object] = {
            "type": "synthesize",
            "id": "b",
            "text": "hello",
        }
        await router._handle_synthesize(msg2, ws)

        assert len(synthesis_calls) == 2

    @pytest.mark.asyncio
    async def test_once_set_dedups_identical_text(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With once=600, the second identical request returns deduped."""
        router, synthesis_calls = self._make_stubbed_router(monkeypatch)
        ws = MagicMock()
        ws.send_json = AsyncMock()

        msg: dict[str, object] = {
            "type": "synthesize",
            "id": "a",
            "text": "wall msg",
            "once": 600,
        }
        await router._handle_synthesize(msg, ws)
        msg2: dict[str, object] = {
            "type": "synthesize",
            "id": "b",
            "text": "wall msg",
            "once": 600,
        }
        await router._handle_synthesize(msg2, ws)

        assert len(synthesis_calls) == 1

        all_calls = ws.send_json.call_args_list
        sent_msgs = [c[0][0] for c in all_calls]
        deduped_msgs = [m for m in sent_msgs if m.get("deduped") is True]
        assert len(deduped_msgs) == 1
        deduped = deduped_msgs[0]
        assert deduped["id"] == "b"
        assert deduped["type"] == "done"
        assert "original_played_at" in deduped
        assert "ttl_seconds_remaining" in deduped
        assert deduped["ttl_seconds_remaining"] > 0

    @pytest.mark.asyncio
    async def test_once_zero_does_not_dedupe(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """once=0 is treated as null per the spec -- must not dedupe."""
        router, synthesis_calls = self._make_stubbed_router(monkeypatch)
        ws = MagicMock()
        ws.send_json = AsyncMock()

        msg: dict[str, object] = {
            "type": "synthesize",
            "id": "a",
            "text": "hello",
            "once": 0,
        }
        await router._handle_synthesize(msg, ws)
        msg2: dict[str, object] = {
            "type": "synthesize",
            "id": "b",
            "text": "hello",
            "once": 0,
        }
        await router._handle_synthesize(msg2, ws)

        assert len(synthesis_calls) == 2


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


# ---------------------------------------------------------------------------
# Music integration tests
# ---------------------------------------------------------------------------


class TestMusicHandlerRegistration:
    """Music handlers must be registered in router.handlers."""

    def test_music_on_registered(self) -> None:
        router = _make_router()
        assert "music_on" in router.handlers

    def test_music_off_registered(self) -> None:
        router = _make_router()
        assert "music_off" in router.handlers

    def test_music_vibe_registered(self) -> None:
        router = _make_router()
        assert "music_vibe" in router.handlers


class TestHandleMusicOn:
    """Router._handle_music_on: ownership transfer and state mutation."""

    def test_sets_music_mode_and_owner(self) -> None:
        music, router = _make_ctx_and_router()
        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {
            "id": "req-1",
            "owner_id": "session-abc",
            "style": "techno",
            "vibe": "focused",
            "vibe_tags": "[calm]",
        }

        asyncio.run(router._handle_music_on(msg, ws))

        assert music.mode == "on"
        assert music.owner == "session-abc"
        assert music.style == "techno"
        assert music.vibe == ("focused", "[calm]")
        assert music.state == "generating"
        assert music.changed.is_set()

    def test_responds_with_generating_status(self) -> None:
        _music, router = _make_ctx_and_router()
        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {
            "id": "req-2",
            "owner_id": "session-xyz",
        }

        asyncio.run(router._handle_music_on(msg, ws))

        ws.send_json.assert_called_once_with(
            {"type": "music_on", "id": "req-2", "status": "generating"}
        )

    def test_ownership_transfer_kills_existing_proc(self) -> None:
        """Transferring ownership kills the previous subprocess."""
        music, router = _make_ctx_and_router()
        music.mode = "on"
        music.owner = "old-session"

        fake_proc = MagicMock()
        fake_proc.returncode = None
        fake_proc.kill = MagicMock()
        fake_proc.wait = AsyncMock(return_value=0)
        music.proc = fake_proc

        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {
            "id": "req-3",
            "owner_id": "new-session",
            "vibe": "happy",
            "vibe_tags": "[warm]",
        }

        asyncio.run(router._handle_music_on(msg, ws))

        fake_proc.kill.assert_called_once()
        assert music.owner == "new-session"
        assert music.proc is None

    def test_preserves_existing_style_when_not_provided(self) -> None:
        music, router = _make_ctx_and_router()
        music.style = "jazz"
        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {
            "id": "req-4",
            "owner_id": "session-1",
            "style": "",
            "vibe": "focused",
        }

        asyncio.run(router._handle_music_on(msg, ws))

        assert music.style == "jazz"


class TestHandleMusicOff:
    """Router._handle_music_off: stops music and resets state."""

    def test_sets_mode_off_and_state_idle(self) -> None:
        music, router = _make_ctx_and_router()
        music.mode = "on"
        music.state = "playing"
        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {"id": "req-off"}

        asyncio.run(router._handle_music_off(msg, ws))

        assert music.mode == "off"
        assert music.state == "idle"
        assert music.changed.is_set()

    def test_responds_with_stopped_status(self) -> None:
        _music, router = _make_ctx_and_router()
        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {"id": "req-off-2"}

        asyncio.run(router._handle_music_off(msg, ws))

        ws.send_json.assert_called_once_with(
            {"type": "music_off", "id": "req-off-2", "status": "stopped"}
        )

    def test_kills_running_subprocess(self) -> None:
        music, router = _make_ctx_and_router()
        fake_proc = MagicMock()
        fake_proc.returncode = None
        fake_proc.kill = MagicMock()
        fake_proc.wait = AsyncMock(return_value=0)
        music.proc = fake_proc

        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {"id": "req-off-3"}

        asyncio.run(router._handle_music_off(msg, ws))

        fake_proc.kill.assert_called_once()
        assert music.proc is None


class TestHandleMusicVibe:
    """Router._handle_music_vibe: ownership check and vibe update."""

    def test_matching_owner_updates_vibe(self) -> None:
        music, router = _make_ctx_and_router()
        music.mode = "on"
        music.owner = "session-abc"
        music.vibe = ("old", "[old-tags]")
        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {
            "id": "vibe-1",
            "owner_id": "session-abc",
            "vibe": "happy",
            "vibe_tags": "[warm]",
        }

        asyncio.run(router._handle_music_vibe(msg, ws))

        assert music.vibe == ("happy", "[warm]")
        assert music.changed.is_set()
        ws.send_json.assert_called_once_with(
            {"type": "music_vibe", "id": "vibe-1", "status": "generating"}
        )

    def test_non_owner_rejected(self) -> None:
        music, router = _make_ctx_and_router()
        music.mode = "on"
        music.owner = "session-abc"
        music.vibe = ("old", "[old-tags]")
        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {
            "id": "vibe-2",
            "owner_id": "session-other",
            "vibe": "happy",
            "vibe_tags": "[warm]",
        }

        asyncio.run(router._handle_music_vibe(msg, ws))

        assert music.vibe == ("old", "[old-tags]")
        ws.send_json.assert_called_once_with(
            {"type": "music_vibe", "id": "vibe-2", "status": "ignored"}
        )

    def test_same_vibe_ignored(self) -> None:
        music, router = _make_ctx_and_router()
        music.owner = "session-abc"
        music.vibe = ("happy", "[warm]")
        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {
            "id": "vibe-3",
            "owner_id": "session-abc",
            "vibe": "happy",
            "vibe_tags": "[warm]",
        }

        asyncio.run(router._handle_music_vibe(msg, ws))

        ws.send_json.assert_called_once_with(
            {"type": "music_vibe", "id": "vibe-3", "status": "ignored"}
        )
        assert not music.changed.is_set()


class TestHandleMusicOnWhilePlaying:
    """Router._handle_music_on: gapless handoff when music is already playing."""

    def test_same_owner_skips_kill(self) -> None:
        """Re-sending music_on while playing (same owner) does not kill proc."""
        music, router = _make_ctx_and_router()
        music.mode = "on"
        music.owner = "session-abc"

        fake_proc = MagicMock()
        fake_proc.returncode = None
        fake_proc.kill = MagicMock()
        fake_proc.wait = AsyncMock(return_value=0)
        music.proc = fake_proc

        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {
            "id": "req-gapless",
            "owner_id": "session-abc",
            "style": "jazz",
            "vibe": "chill",
            "vibe_tags": "[mellow]",
        }

        asyncio.run(router._handle_music_on(msg, ws))

        fake_proc.kill.assert_not_called()
        assert music.mode == "on"
        assert music.style == "jazz"
        assert music.vibe == ("chill", "[mellow]")
        assert music.changed.is_set()

    def test_different_owner_kills_proc(self) -> None:
        """Ownership transfer while playing kills the existing proc."""
        music, router = _make_ctx_and_router()
        music.mode = "on"
        music.owner = "old-owner"

        fake_proc = MagicMock()
        fake_proc.returncode = None
        fake_proc.kill = MagicMock()
        fake_proc.wait = AsyncMock(return_value=0)
        music.proc = fake_proc

        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {
            "id": "req-transfer",
            "owner_id": "new-owner",
            "vibe": "upbeat",
            "vibe_tags": "[energetic]",
        }

        asyncio.run(router._handle_music_on(msg, ws))

        fake_proc.kill.assert_called_once()
        assert music.owner == "new-owner"
        assert music.proc is None


class TestHandleMusicNext:
    """Router._handle_music_next: skip-track handler tests."""

    def test_signals_music_changed(self) -> None:
        music, router = _make_ctx_and_router()
        music.mode = "on"
        music.owner = "session-abc"
        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {
            "id": "next-1",
            "owner_id": "session-abc",
        }

        asyncio.run(router._handle_music_next(msg, ws))

        assert music.changed.is_set()
        ws.send_json.assert_called_once_with(
            {"type": "music_next", "id": "next-1", "status": "generating"}
        )

    def test_ignored_when_music_off(self) -> None:
        music, router = _make_ctx_and_router()
        music.mode = "off"
        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {
            "id": "next-2",
            "owner_id": "session-abc",
        }

        asyncio.run(router._handle_music_next(msg, ws))

        assert not music.changed.is_set()
        ws.send_json.assert_called_once_with(
            {"type": "music_next", "id": "next-2", "status": "ignored"}
        )

    def test_clears_replay_flag(self) -> None:
        music, router = _make_ctx_and_router()
        music.mode = "on"
        music.owner = "session-abc"
        music.replay = True
        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {
            "id": "next-3",
            "owner_id": "session-abc",
        }

        asyncio.run(router._handle_music_next(msg, ws))

        assert music.replay is False
        assert music.changed.is_set()

    def test_error_when_no_owner_id(self) -> None:
        music, router = _make_ctx_and_router()
        music.mode = "on"
        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {"id": "next-4"}

        asyncio.run(router._handle_music_next(msg, ws))

        ws.send_json.assert_called_once_with(
            {"type": "error", "id": "next-4", "message": "owner_id is required"}
        )

    def test_music_next_registered(self) -> None:
        router = _make_router()
        assert "music_next" in router.handlers


class TestEmptyOwnerIdRejection:
    """Handlers must reject empty owner_id to prevent ownership spoofing."""

    def test_music_on_rejects_empty_owner_id(self) -> None:
        music, router = _make_ctx_and_router()
        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {"id": "empty-1", "owner_id": "", "vibe": "focused"}

        asyncio.run(router._handle_music_on(msg, ws))

        ws.send_json.assert_called_once_with(
            {"type": "error", "id": "empty-1", "message": "owner_id is required"}
        )
        assert music.mode == "off"

    def test_music_on_rejects_missing_owner_id(self) -> None:
        music, router = _make_ctx_and_router()
        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {"id": "empty-2", "vibe": "focused"}

        asyncio.run(router._handle_music_on(msg, ws))

        ws.send_json.assert_called_once_with(
            {"type": "error", "id": "empty-2", "message": "owner_id is required"}
        )
        assert music.mode == "off"

    def test_music_vibe_rejects_empty_owner_id(self) -> None:
        music, router = _make_ctx_and_router()
        music.mode = "on"
        music.owner = "real-session"
        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {
            "id": "empty-3",
            "owner_id": "",
            "vibe": "happy",
        }

        asyncio.run(router._handle_music_vibe(msg, ws))

        ws.send_json.assert_called_once_with(
            {"type": "error", "id": "empty-3", "message": "owner_id is required"}
        )
        assert music.vibe == ("", "")

    def test_music_vibe_rejects_missing_owner_id(self) -> None:
        music, router = _make_ctx_and_router()
        music.mode = "on"
        music.owner = "real-session"
        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {"id": "empty-4", "vibe": "happy"}

        asyncio.run(router._handle_music_vibe(msg, ws))

        ws.send_json.assert_called_once_with(
            {"type": "error", "id": "empty-4", "message": "owner_id is required"}
        )
        assert music.vibe == ("", "")


class TestAutoTrackName:
    """TrackGenerator.auto_track_name derives vibe-style-YYYYMMDD-HHMM patterns."""

    def _tg(self) -> TrackGenerator:
        return TrackGenerator(Path("/tmp/vox-test-music"))

    def test_with_vibe_and_style(self) -> None:
        name = self._tg().auto_track_name("happy", "techno")
        assert name.startswith("happy-techno-")
        parts = name.split("-")
        assert len(parts[-2]) == 8  # YYYYMMDD
        assert len(parts[-1]) == 4  # HHMM

    def test_no_vibe_uses_ambient(self) -> None:
        name = self._tg().auto_track_name("", "")
        assert name.startswith("ambient-mix-")

    def test_no_style_uses_mix(self) -> None:
        name = self._tg().auto_track_name("chill", "")
        assert name.startswith("chill-mix-")


class TestMusicSchedulerTrackName:
    """MusicScheduler.track_name defaults to empty string."""

    def test_default(self) -> None:
        scheduler = MusicScheduler(TrackGenerator(Path("/tmp/vox-test-music")))
        assert scheduler.track_name == ""

    def test_music_replay_default(self) -> None:
        scheduler = MusicScheduler(TrackGenerator(Path("/tmp/vox-test-music")))
        assert scheduler.replay is False


class TestHandleMusicOnWithName:
    """Router._handle_music_on with name field for track naming and replay."""

    def test_replay_existing_track(self, tmp_path: Path) -> None:
        """When name matches an existing file, replay without generation."""
        music_dir = tmp_path / "music"
        music_dir.mkdir()
        track = music_dir / "my_focus.mp3"
        track.write_bytes(b"fake-music")

        tg = TrackGenerator(music_dir)
        music, router = _make_ctx_and_router()
        # Replace the track generator so it uses tmp_path.
        router._track_generator = tg

        ws = AsyncMock()
        msg: dict[str, object] = {
            "type": "music_on",
            "id": "req-name-1",
            "owner_id": "session-x",
            "name": "my focus",
        }

        asyncio.run(router._handle_music_on(msg, ws))

        assert music.mode == "on"
        assert music.track == track
        assert music.track_name == "my_focus"
        assert music.state == "playing"
        assert music.replay is True

        resp = ws.send_json.call_args[0][0]
        assert resp["status"] == "playing"
        assert resp["name"] == "my_focus"
        assert str(track) in resp["track"]

    def test_name_not_found_generates(self, tmp_path: Path) -> None:
        """When name does not match existing file, proceed to generation."""
        music_dir = tmp_path / "music"
        music_dir.mkdir()

        tg = TrackGenerator(music_dir)
        music, router = _make_ctx_and_router()
        router._track_generator = tg

        ws = AsyncMock()
        msg: dict[str, object] = {
            "type": "music_on",
            "id": "req-name-2",
            "owner_id": "session-y",
            "name": "new track",
        }

        asyncio.run(router._handle_music_on(msg, ws))

        assert music.mode == "on"
        assert music.track_name == "new_track"
        assert music.state == "generating"
        assert music.changed.is_set()

        resp = ws.send_json.call_args[0][0]
        assert resp["status"] == "generating"

    def test_no_name_clears_track_name(self) -> None:
        """When no name is given, track_name is empty (auto-naming in generation)."""
        music, router = _make_ctx_and_router()
        ws = AsyncMock()
        msg: dict[str, object] = {
            "type": "music_on",
            "id": "req-no-name",
            "owner_id": "session-z",
        }

        asyncio.run(router._handle_music_on(msg, ws))

        assert music.track_name == ""
        assert music.state == "generating"

    def test_empty_slugified_name_returns_error(self) -> None:
        """Name that slugifies to empty string returns error."""
        music, router = _make_ctx_and_router()
        ws = AsyncMock()
        msg: dict[str, object] = {
            "type": "music_on",
            "id": "req-bad-name",
            "owner_id": "session-q",
            "name": "---",
        }

        asyncio.run(router._handle_music_on(msg, ws))

        resp = ws.send_json.call_args[0][0]
        assert resp["type"] == "error"
        assert "invalid track name" in resp["message"]
        assert music.mode == "off"


class TestHandleMusicPlay:
    """Router._handle_music_play: replay saved tracks by name."""

    def test_play_existing_track(self, tmp_path: Path) -> None:
        music_dir = tmp_path / "music"
        music_dir.mkdir()
        track = music_dir / "chill_vibes.mp3"
        track.write_bytes(b"fake-music")

        tg = TrackGenerator(music_dir)
        music, router = _make_ctx_and_router()
        router._track_generator = tg

        ws = AsyncMock()
        msg: dict[str, object] = {
            "type": "music_play",
            "id": "play-1",
            "name": "chill vibes",
            "owner_id": "session-a",
        }

        asyncio.run(router._handle_music_play(msg, ws))

        assert music.mode == "on"
        assert music.track == track
        assert music.track_name == "chill_vibes"
        assert music.state == "playing"
        assert music.replay is True

        resp = ws.send_json.call_args[0][0]
        assert resp["type"] == "music_play"
        assert resp["status"] == "playing"
        assert resp["name"] == "chill_vibes"

    def test_play_not_found(self, tmp_path: Path) -> None:
        music_dir = tmp_path / "music"
        music_dir.mkdir()

        tg = TrackGenerator(music_dir)
        _music, router = _make_ctx_and_router()
        router._track_generator = tg

        ws = AsyncMock()
        msg: dict[str, object] = {
            "type": "music_play",
            "id": "play-2",
            "name": "nonexistent",
            "owner_id": "session-b",
        }

        asyncio.run(router._handle_music_play(msg, ws))

        resp = ws.send_json.call_args[0][0]
        assert resp["type"] == "error"
        assert "not found" in resp["message"]

    def test_play_missing_name(self) -> None:
        _music, router = _make_ctx_and_router()
        ws = AsyncMock()
        msg: dict[str, object] = {
            "type": "music_play",
            "id": "play-3",
            "owner_id": "session-c",
        }

        asyncio.run(router._handle_music_play(msg, ws))

        resp = ws.send_json.call_args[0][0]
        assert resp["type"] == "error"
        assert "name is required" in resp["message"]

    def test_play_missing_owner_id(self) -> None:
        _music, router = _make_ctx_and_router()
        ws = AsyncMock()
        msg: dict[str, object] = {
            "type": "music_play",
            "id": "play-4",
            "name": "test",
        }

        asyncio.run(router._handle_music_play(msg, ws))

        resp = ws.send_json.call_args[0][0]
        assert resp["type"] == "error"
        assert "owner_id is required" in resp["message"]

    def test_empty_slugified_name_returns_error(self) -> None:
        """Name that slugifies to empty string returns error."""
        _music, router = _make_ctx_and_router()
        ws = AsyncMock()
        msg: dict[str, object] = {
            "type": "music_play",
            "id": "play-bad",
            "name": "---",
            "owner_id": "session-q",
        }

        asyncio.run(router._handle_music_play(msg, ws))

        resp = ws.send_json.call_args[0][0]
        assert resp["type"] == "error"
        assert "invalid track name" in resp["message"]


class TestHandleMusicList:
    """Router._handle_music_list: returns saved tracks with metadata."""

    def test_list_empty_dir(self, tmp_path: Path) -> None:
        music_dir = tmp_path / "music"
        music_dir.mkdir()

        tg = TrackGenerator(music_dir)
        _music, router = _make_ctx_and_router()
        router._track_generator = tg

        ws = AsyncMock()
        msg: dict[str, object] = {"type": "music_list", "id": "list-1"}

        asyncio.run(router._handle_music_list(msg, ws))

        resp = ws.send_json.call_args[0][0]
        assert resp["type"] == "music_list"
        assert resp["tracks"] == []

    def test_list_with_tracks(self, tmp_path: Path) -> None:
        music_dir = tmp_path / "music"
        music_dir.mkdir()
        (music_dir / "alpha.mp3").write_bytes(b"a" * 1024)
        (music_dir / "beta.mp3").write_bytes(b"b" * 2048)

        tg = TrackGenerator(music_dir)
        _music, router = _make_ctx_and_router()
        router._track_generator = tg

        ws = AsyncMock()
        msg: dict[str, object] = {"type": "music_list", "id": "list-2"}

        asyncio.run(router._handle_music_list(msg, ws))

        resp = ws.send_json.call_args[0][0]
        assert resp["type"] == "music_list"
        assert len(resp["tracks"]) == 2
        names = [t["name"] for t in resp["tracks"]]
        assert "alpha" in names
        assert "beta" in names
        for t in resp["tracks"]:
            assert "size_bytes" in t
            assert "modified" in t
            assert "path" in t

    def test_list_nonexistent_dir(self, tmp_path: Path) -> None:
        music_dir = tmp_path / "music_missing"

        tg = TrackGenerator(music_dir)
        _music, router = _make_ctx_and_router()
        router._track_generator = tg

        ws = AsyncMock()
        msg: dict[str, object] = {"type": "music_list", "id": "list-3"}

        asyncio.run(router._handle_music_list(msg, ws))

        resp = ws.send_json.call_args[0][0]
        assert resp["type"] == "music_list"
        assert resp["tracks"] == []


class TestHandlerRegistration:
    """All handlers are registered in the router."""

    def test_music_play_registered(self) -> None:
        router = _make_router()
        assert "music_play" in router.handlers

    def test_music_list_registered(self) -> None:
        router = _make_router()
        assert "music_list" in router.handlers

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
