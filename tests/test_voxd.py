"""Tests for punt_vox.voxd observability and direct-play dispatch."""
# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from punt_vox.voxd import (
    DaemonContext,
    PlaybackItem,
    _auto_track_name,
    _handle_music_list,
    _handle_music_next,
    _handle_music_off,
    _handle_music_on,
    _handle_music_play,
    _handle_music_vibe,
    _handle_synthesize,
    _health_payload_full,
    _health_payload_minimal,
    _health_route,
)


def _make_ctx() -> DaemonContext:
    """Build a DaemonContext without touching real files or auth."""
    return DaemonContext(auth_token=None, port=0)


class TestHealthPayloadFull:
    """The authenticated WS health payload exposes audio state for vox doctor."""

    def test_includes_audio_env_and_player_binary(self) -> None:
        ctx = _make_ctx()
        payload = _health_payload_full(ctx)

        assert "audio_env" in payload
        assert "player_binary" in payload
        assert "last_playback" in payload
        audio_env = cast("dict[str, str]", payload["audio_env"])
        assert "XDG_RUNTIME_DIR" in audio_env
        assert "PULSE_SERVER" in audio_env
        assert "DBUS_SESSION_BUS_ADDRESS" in audio_env

    def test_includes_daemon_version_matching_installed_package(self) -> None:
        """Authenticated payload carries daemon_version from importlib.metadata.

        This is the server-side half of the vox-nmb fix: ``vox doctor``
        reads this field and compares it against the wheel installed on
        disk, warning the user if they diverge. The field must match
        ``importlib.metadata.version("punt-vox")`` so the comparison is
        meaningful.
        """
        import importlib.metadata

        ctx = _make_ctx()
        payload = _health_payload_full(ctx)

        assert "daemon_version" in payload
        # The context caches the version at init — verify it matches the
        # installed wheel.
        try:
            expected = importlib.metadata.version("punt-vox")
        except importlib.metadata.PackageNotFoundError:
            from punt_vox import __version__

            expected = __version__
        assert payload["daemon_version"] == expected

    def test_daemon_version_cached_on_context(self) -> None:
        """DaemonContext caches the version once at init — not per request."""
        ctx = _make_ctx()
        # Mutate the cached value; subsequent health calls must reflect
        # the cached state, proving there's no per-call metadata lookup.
        ctx.daemon_version = "99.99.99-test-sentinel"
        payload = _health_payload_full(ctx)
        assert payload["daemon_version"] == "99.99.99-test-sentinel"

    def test_includes_pid(self) -> None:
        """Authenticated payload includes os.getpid() so restart can verify.

        ``vox daemon restart`` reads this field to confirm the daemon
        came back up and surfaces the new pid in the success message.
        """
        import os as _os

        ctx = _make_ctx()
        payload = _health_payload_full(ctx)
        assert "pid" in payload
        assert payload["pid"] == _os.getpid()

    def test_unset_audio_env_uses_sentinel(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Distinguish "unset" from "set to empty string" — diagnosing
        # PulseAudio failures requires knowing which one we have.
        for key in ("XDG_RUNTIME_DIR", "PULSE_SERVER", "DBUS_SESSION_BUS_ADDRESS"):
            monkeypatch.delenv(key, raising=False)
        ctx = _make_ctx()
        payload = _health_payload_full(ctx)
        audio_env = cast("dict[str, str]", payload["audio_env"])
        assert audio_env["XDG_RUNTIME_DIR"] == "<unset>"
        assert audio_env["PULSE_SERVER"] == "<unset>"
        assert audio_env["DBUS_SESSION_BUS_ADDRESS"] == "<unset>"

    def test_last_playback_reflects_context(self, tmp_path: Path) -> None:
        ctx = _make_ctx()
        ctx.last_playback = {
            "file": str(tmp_path / "x.mp3"),
            "rc": 0,
            "elapsed_s": 1.23,
            "stderr": "",
            "ts": 0.0,
        }
        payload = _health_payload_full(ctx)
        assert payload["last_playback"] == ctx.last_playback


class TestHealthPayloadMinimal:
    """Unauthenticated HTTP /health must not leak sensitive diagnostic state."""

    def test_excludes_audio_env_and_last_playback(self) -> None:
        ctx = _make_ctx()
        payload = _health_payload_minimal(ctx)

        assert "audio_env" not in payload
        assert "player_binary" not in payload
        assert "last_playback" not in payload
        # Public fields are still present.
        assert payload["status"] == "ok"
        assert "uptime_seconds" in payload
        assert "queued" in payload

    def test_excludes_daemon_version_and_pid(self) -> None:
        """Public /health must not fingerprint the running version or pid.

        Exposing ``daemon_version`` to anonymous callers makes it trivial
        to identify stale daemons running exploitable versions.
        ``pid`` is likewise a diagnostic-only detail. Both fields are
        authenticated-only.
        """
        ctx = _make_ctx()
        payload = _health_payload_minimal(ctx)

        assert "daemon_version" not in payload
        assert "pid" not in payload

    def test_http_health_route_excludes_daemon_version(self) -> None:
        """The HTTP /health response body must not carry daemon_version."""
        import json

        ctx = _make_ctx()
        ctx.daemon_version = "1.2.3-fingerprint-sentinel"
        request = MagicMock()
        request.app.state.ctx = ctx

        response = asyncio.run(_health_route(request))
        body = json.loads(bytes(response.body))

        assert "daemon_version" not in body
        assert "1.2.3-fingerprint-sentinel" not in bytes(response.body).decode()

    def test_http_health_route_returns_minimal_payload(self) -> None:
        import json

        ctx = _make_ctx()
        ctx.last_playback = {
            "file": "/tmp/x.mp3",
            "rc": 0,
            "elapsed_s": 0.5,
            "stderr": "secret stderr",
            "ts": 0.0,
        }
        request = MagicMock()
        request.app.state.ctx = ctx

        response = asyncio.run(_health_route(request))
        raw = bytes(response.body)
        body = json.loads(raw)

        assert "audio_env" not in body
        assert "last_playback" not in body
        assert "secret stderr" not in raw.decode()


class TestHandleSynthesizeShortCircuit:
    """``_handle_synthesize`` skips _try_direct_play for cloud providers."""

    def test_cloud_provider_skips_direct_play(self) -> None:
        ctx = _make_ctx()
        websocket = MagicMock()
        websocket.send_json = AsyncMock()
        msg: dict[str, object] = {
            "id": "1",
            "text": "hello",
            "provider": "elevenlabs",
        }

        # Force _synthesize_to_file to raise so the handler exits via the
        # error branch -- we only care that direct-play was never invoked.
        with (
            patch(
                "punt_vox.voxd._monolith._try_direct_play",
                AsyncMock(return_value=None),
            ) as mock_direct,
            patch(
                "punt_vox.voxd._monolith._synthesize_to_file",
                AsyncMock(side_effect=RuntimeError("stop here")),
            ),
        ):
            asyncio.run(_handle_synthesize(msg, websocket, ctx))

        mock_direct.assert_not_called()

    def test_local_provider_calls_direct_play(self) -> None:
        ctx = _make_ctx()
        websocket = MagicMock()
        websocket.send_json = AsyncMock()
        msg: dict[str, object] = {"id": "2", "text": "hello", "provider": "espeak"}

        with patch(
            "punt_vox.voxd._monolith._try_direct_play",
            AsyncMock(return_value=0),
        ) as mock_direct:
            asyncio.run(_handle_synthesize(msg, websocket, ctx))

        mock_direct.assert_called_once()
        call_kwargs = mock_direct.call_args.kwargs
        assert call_kwargs["provider_name"] == "espeak"


class TestHandleSynthesizeOnceFlag:
    """Integration tests for _handle_synthesize with the once flag."""

    @staticmethod
    def _install_handler_stubs(
        monkeypatch: pytest.MonkeyPatch,
        ctx: DaemonContext,
    ) -> list[str]:
        """Wire up a minimal fake for everything downstream of the dedup gate.

        Returns a list that accumulates the text of every call that reaches
        _synthesize_to_file — i.e. every call NOT short-circuited by the
        once-flag dedup. The playback queue is replaced with a stub whose
        ``put`` sets the ``PlaybackItem.notify`` event immediately, so the
        handler's ``await done_event.wait()`` returns without hanging.
        """
        synthesis_calls: list[str] = []

        async def fake_synthesize(*args: object, **_kwargs: object) -> Path:
            synthesis_calls.append(str(args[0]))
            return Path("/tmp/fake.mp3")

        monkeypatch.setattr(
            "punt_vox.voxd._monolith._synthesize_to_file", fake_synthesize
        )
        monkeypatch.setattr("punt_vox.voxd._monolith._LOCAL_PROVIDERS", set[str]())
        monkeypatch.setattr(
            "punt_vox.voxd._monolith.auto_detect_provider", lambda: "elevenlabs"
        )

        class _InstantPlaybackQueue:
            async def put(self, item: PlaybackItem) -> None:
                # Mark the item's done event so the handler's wait() returns.
                item.notify.set()

        ctx._playback._queue = _InstantPlaybackQueue()  # type: ignore[assignment]
        return synthesis_calls

    @pytest.mark.asyncio
    async def test_once_null_does_not_dedupe(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without once, identical requests both proceed (regression).

        The legacy always-on speech dedup was removed in vox-0e9.
        Two identical synthesize calls without an once flag should
        BOTH play.
        """
        ctx = _make_ctx()
        ws = MagicMock()
        ws.send_json = AsyncMock()
        synthesis_calls = self._install_handler_stubs(monkeypatch, ctx)

        msg: dict[str, object] = {
            "type": "synthesize",
            "id": "a",
            "text": "hello",
        }
        await _handle_synthesize(msg, ws, ctx)
        msg2: dict[str, object] = {
            "type": "synthesize",
            "id": "b",
            "text": "hello",
        }
        await _handle_synthesize(msg2, ws, ctx)

        # Both calls reached _synthesize_to_file.
        assert len(synthesis_calls) == 2

    @pytest.mark.asyncio
    async def test_once_set_dedups_identical_text(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With once=600, the second identical request returns deduped."""
        ctx = _make_ctx()
        ws = MagicMock()
        ws.send_json = AsyncMock()
        synthesis_calls = self._install_handler_stubs(monkeypatch, ctx)

        msg: dict[str, object] = {
            "type": "synthesize",
            "id": "a",
            "text": "wall msg",
            "once": 600,
        }
        await _handle_synthesize(msg, ws, ctx)
        msg2: dict[str, object] = {
            "type": "synthesize",
            "id": "b",
            "text": "wall msg",
            "once": 600,
        }
        await _handle_synthesize(msg2, ws, ctx)

        # First call hit synthesis; second call short-circuited.
        assert len(synthesis_calls) == 1

        # Inspect the second call's done message — should carry deduped fields.
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
        """once=0 is treated as null per the spec — must not dedupe."""
        ctx = _make_ctx()
        ws = MagicMock()
        ws.send_json = AsyncMock()
        synthesis_calls = self._install_handler_stubs(monkeypatch, ctx)

        msg: dict[str, object] = {
            "type": "synthesize",
            "id": "a",
            "text": "hello",
            "once": 0,
        }
        await _handle_synthesize(msg, ws, ctx)
        msg2: dict[str, object] = {
            "type": "synthesize",
            "id": "b",
            "text": "hello",
            "once": 0,
        }
        await _handle_synthesize(msg2, ws, ctx)

        # Both calls reached synthesis (once=0 treated as no dedup).
        assert len(synthesis_calls) == 2


class TestWsRoutePeerClose:
    """_ws_route must exit quietly when the peer closes mid-receive (vox-ewh).

    After the vox-ehf fix in 4.3.0, chime/unmute clients return on the
    ``"playing"`` ack and close the WebSocket while voxd's ``_ws_route`` is
    still sitting in its ``while True: receive_text()`` loop. The handler's
    trailing ``contextlib.suppress(WebSocketDisconnect, RuntimeError)``
    send of the stale ``"done"`` message lands on the peer-closed socket,
    Starlette catches the OSError and transitions ``application_state`` to
    ``DISCONNECTED`` (raising ``WebSocketDisconnect(1006)``, which the
    suppress swallows). The next ``receive_text()`` in the outer loop would
    then raise ``RuntimeError('WebSocket is not connected. Need to call
    "accept" first.')`` — not ``WebSocketDisconnect`` — and fall through to
    ``except Exception: logger.exception("WebSocket error")``, logging a
    full traceback on every chime, unmute, and recap. The fix preempts
    the RuntimeError by checking ``websocket.application_state`` at the
    top of the receive loop and breaking out cleanly when it is no longer
    ``CONNECTED``. The outer ``except WebSocketDisconnect`` clause stays
    exactly as narrow as it was before; the ``except Exception`` clause
    still catches genuine unexpected errors from ``receive_text``,
    ``json.loads``, or any handler.
    """

    @pytest.mark.asyncio
    async def test_ws_route_state_check_preempts_disconnected_receive(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """State check preempts ``receive_text`` on a peer-closed socket.

        Drives ``_ws_route`` directly with a fake WebSocket whose
        ``application_state`` is ``DISCONNECTED`` — the exact state
        Starlette leaves the socket in after a handler's trailing
        suppress-send lands on a peer-closed peer. The loop must break
        without calling ``receive_text``, must not log a ``"WebSocket
        error"`` record, and must still decrement ``client_count`` in its
        ``finally`` branch.
        """
        from starlette.websockets import WebSocketState

        from punt_vox.voxd import _ws_route

        ctx = DaemonContext(auth_token=None, port=0)
        ctx.client_count = 0

        class _FakeApp:
            def __init__(self, daemon_ctx: DaemonContext) -> None:
                self.state = type("S", (), {"ctx": daemon_ctx})()

        fake_app = _FakeApp(ctx)

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
        fake_ws.app = fake_app
        fake_ws.headers = {}
        fake_ws.query_params = {}
        fake_ws.accept = _accept
        fake_ws.close = _close
        fake_ws.receive_text = _receive_text
        fake_ws.application_state = WebSocketState.DISCONNECTED

        with caplog.at_level(logging.ERROR, logger="punt_vox.voxd"):
            await _ws_route(cast("object", fake_ws))  # type: ignore[arg-type]

        ws_error_records = [
            rec
            for rec in caplog.records
            if rec.levelno == logging.ERROR
            and rec.name == "punt_vox.voxd._monolith"
            and "WebSocket error" in rec.getMessage()
        ]
        assert ws_error_records == []
        assert receive_calls == 0
        # The ``finally`` branch must still decrement client_count.
        # Entry increments from 0 to 1, exit decrements back to 0.
        assert ctx.client_count == 0

    @pytest.mark.asyncio
    async def test_ws_route_logs_error_when_receive_raises_unexpected_runtimeerror(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Unexpected ``RuntimeError`` from ``receive_text`` still logs an error.

        Documents the narrowing guarantee: the vox-ewh fix preempts only
        the peer-closed-state ``RuntimeError``. A genuine ``RuntimeError``
        raised from ``receive_text`` while the socket is still reported as
        ``CONNECTED`` (race, bug, unexpected Starlette state) must NOT be
        silently swallowed — it still hits the outer
        ``except Exception: logger.exception("WebSocket error")`` branch.
        """
        from starlette.websockets import WebSocketState

        from punt_vox.voxd import _ws_route

        ctx = DaemonContext(auth_token=None, port=0)
        ctx.client_count = 0

        class _FakeApp:
            def __init__(self, daemon_ctx: DaemonContext) -> None:
                self.state = type("S", (), {"ctx": daemon_ctx})()

        fake_app = _FakeApp(ctx)

        async def _accept() -> None:
            return None

        async def _close(code: int = 1000) -> None:
            return None

        async def _receive_text() -> str:
            raise RuntimeError("something else entirely")

        fake_ws = MagicMock()
        fake_ws.app = fake_app
        fake_ws.headers = {}
        fake_ws.query_params = {}
        fake_ws.accept = _accept
        fake_ws.close = _close
        fake_ws.receive_text = _receive_text
        fake_ws.application_state = WebSocketState.CONNECTED

        with caplog.at_level(logging.ERROR, logger="punt_vox.voxd"):
            await _ws_route(cast("object", fake_ws))  # type: ignore[arg-type]

        ws_error_records = [
            rec
            for rec in caplog.records
            if rec.levelno == logging.ERROR
            and rec.name == "punt_vox.voxd._monolith"
            and "WebSocket error" in rec.getMessage()
        ]
        assert len(ws_error_records) == 1
        # The ``finally`` branch must still decrement client_count.
        assert ctx.client_count == 0


# ---------------------------------------------------------------------------
# Music integration tests
# ---------------------------------------------------------------------------


class TestMusicHandlerRegistration:
    """Music handlers must be registered in _HANDLERS."""

    def test_music_on_registered(self) -> None:
        from punt_vox.voxd import _HANDLERS

        assert "music_on" in _HANDLERS
        assert _HANDLERS["music_on"] is _handle_music_on

    def test_music_off_registered(self) -> None:
        from punt_vox.voxd import _HANDLERS

        assert "music_off" in _HANDLERS
        assert _HANDLERS["music_off"] is _handle_music_off

    def test_music_vibe_registered(self) -> None:
        from punt_vox.voxd import _HANDLERS

        assert "music_vibe" in _HANDLERS
        assert _HANDLERS["music_vibe"] is _handle_music_vibe


class TestHandleMusicOn:
    """_handle_music_on: ownership transfer and state mutation."""

    def test_sets_music_mode_and_owner(self) -> None:
        ctx = _make_ctx()
        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {
            "id": "req-1",
            "owner_id": "session-abc",
            "style": "techno",
            "vibe": "focused",
            "vibe_tags": "[calm]",
        }

        asyncio.run(_handle_music_on(msg, ws, ctx))

        assert ctx.music_mode == "on"
        assert ctx.music_owner == "session-abc"
        assert ctx.music_style == "techno"
        assert ctx.music_vibe == ("focused", "[calm]")
        assert ctx.music_state == "generating"
        assert ctx.music_changed.is_set()

    def test_responds_with_generating_status(self) -> None:
        ctx = _make_ctx()
        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {
            "id": "req-2",
            "owner_id": "session-xyz",
        }

        asyncio.run(_handle_music_on(msg, ws, ctx))

        ws.send_json.assert_called_once_with(
            {"type": "music_on", "id": "req-2", "status": "generating"}
        )

    def test_ownership_transfer_kills_existing_proc(self) -> None:
        """Transferring ownership kills the previous subprocess."""
        ctx = _make_ctx()
        ctx.music_mode = "on"
        ctx.music_owner = "old-session"

        # Simulate a running music subprocess.
        fake_proc = MagicMock()
        fake_proc.returncode = None
        fake_proc.kill = MagicMock()
        fake_proc.wait = AsyncMock(return_value=0)
        ctx.music_proc = fake_proc

        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {
            "id": "req-3",
            "owner_id": "new-session",
            "vibe": "happy",
            "vibe_tags": "[warm]",
        }

        asyncio.run(_handle_music_on(msg, ws, ctx))

        fake_proc.kill.assert_called_once()
        assert ctx.music_owner == "new-session"
        assert ctx.music_proc is None

    def test_preserves_existing_style_when_not_provided(self) -> None:
        ctx = _make_ctx()
        ctx.music_style = "jazz"
        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {
            "id": "req-4",
            "owner_id": "session-1",
            "style": "",
            "vibe": "focused",
        }

        asyncio.run(_handle_music_on(msg, ws, ctx))

        assert ctx.music_style == "jazz"


class TestHandleMusicOff:
    """_handle_music_off: stops music and resets state."""

    def test_sets_mode_off_and_state_idle(self) -> None:
        ctx = _make_ctx()
        ctx.music_mode = "on"
        ctx.music_state = "playing"
        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {"id": "req-off"}

        asyncio.run(_handle_music_off(msg, ws, ctx))

        assert ctx.music_mode == "off"
        assert ctx.music_state == "idle"
        assert ctx.music_changed.is_set()

    def test_responds_with_stopped_status(self) -> None:
        ctx = _make_ctx()
        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {"id": "req-off-2"}

        asyncio.run(_handle_music_off(msg, ws, ctx))

        ws.send_json.assert_called_once_with(
            {"type": "music_off", "id": "req-off-2", "status": "stopped"}
        )

    def test_kills_running_subprocess(self) -> None:
        ctx = _make_ctx()
        fake_proc = MagicMock()
        fake_proc.returncode = None
        fake_proc.kill = MagicMock()
        fake_proc.wait = AsyncMock(return_value=0)
        ctx.music_proc = fake_proc

        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {"id": "req-off-3"}

        asyncio.run(_handle_music_off(msg, ws, ctx))

        fake_proc.kill.assert_called_once()
        assert ctx.music_proc is None


class TestHandleMusicVibe:
    """_handle_music_vibe: ownership check and vibe update."""

    def test_matching_owner_updates_vibe(self) -> None:
        ctx = _make_ctx()
        ctx.music_mode = "on"
        ctx.music_owner = "session-abc"
        ctx.music_vibe = ("old", "[old-tags]")
        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {
            "id": "vibe-1",
            "owner_id": "session-abc",
            "vibe": "happy",
            "vibe_tags": "[warm]",
        }

        asyncio.run(_handle_music_vibe(msg, ws, ctx))

        assert ctx.music_vibe == ("happy", "[warm]")
        assert ctx.music_changed.is_set()
        ws.send_json.assert_called_once_with(
            {"type": "music_vibe", "id": "vibe-1", "status": "generating"}
        )

    def test_non_owner_rejected(self) -> None:
        ctx = _make_ctx()
        ctx.music_mode = "on"
        ctx.music_owner = "session-abc"
        ctx.music_vibe = ("old", "[old-tags]")
        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {
            "id": "vibe-2",
            "owner_id": "session-other",
            "vibe": "happy",
            "vibe_tags": "[warm]",
        }

        asyncio.run(_handle_music_vibe(msg, ws, ctx))

        # Vibe unchanged.
        assert ctx.music_vibe == ("old", "[old-tags]")
        ws.send_json.assert_called_once_with(
            {"type": "music_vibe", "id": "vibe-2", "status": "ignored"}
        )

    def test_same_vibe_ignored(self) -> None:
        ctx = _make_ctx()
        ctx.music_owner = "session-abc"
        ctx.music_vibe = ("happy", "[warm]")
        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {
            "id": "vibe-3",
            "owner_id": "session-abc",
            "vibe": "happy",
            "vibe_tags": "[warm]",
        }

        asyncio.run(_handle_music_vibe(msg, ws, ctx))

        ws.send_json.assert_called_once_with(
            {"type": "music_vibe", "id": "vibe-3", "status": "ignored"}
        )
        assert not ctx.music_changed.is_set()


class TestHandleMusicOnWhilePlaying:
    """_handle_music_on: gapless handoff when music is already playing."""

    def test_same_owner_skips_kill(self) -> None:
        """Re-sending music_on while playing (same owner) does not kill proc."""
        ctx = _make_ctx()
        ctx.music_mode = "on"
        ctx.music_owner = "session-abc"

        fake_proc = MagicMock()
        fake_proc.returncode = None
        fake_proc.kill = MagicMock()
        fake_proc.wait = AsyncMock(return_value=0)
        ctx.music_proc = fake_proc

        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {
            "id": "req-gapless",
            "owner_id": "session-abc",
            "style": "jazz",
            "vibe": "chill",
            "vibe_tags": "[mellow]",
        }

        asyncio.run(_handle_music_on(msg, ws, ctx))

        # Proc was NOT killed — gapless handoff via MusicLoop.
        fake_proc.kill.assert_not_called()
        assert ctx.music_mode == "on"
        assert ctx.music_style == "jazz"
        assert ctx.music_vibe == ("chill", "[mellow]")
        assert ctx.music_changed.is_set()

    def test_different_owner_kills_proc(self) -> None:
        """Ownership transfer while playing kills the existing proc."""
        ctx = _make_ctx()
        ctx.music_mode = "on"
        ctx.music_owner = "old-owner"

        fake_proc = MagicMock()
        fake_proc.returncode = None
        fake_proc.kill = MagicMock()
        fake_proc.wait = AsyncMock(return_value=0)
        ctx.music_proc = fake_proc

        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {
            "id": "req-transfer",
            "owner_id": "new-owner",
            "vibe": "upbeat",
            "vibe_tags": "[energetic]",
        }

        asyncio.run(_handle_music_on(msg, ws, ctx))

        fake_proc.kill.assert_called_once()
        assert ctx.music_owner == "new-owner"
        assert ctx.music_proc is None


class TestHandleMusicNext:
    """_handle_music_next: skip-track handler tests."""

    def test_signals_music_changed(self) -> None:
        ctx = _make_ctx()
        ctx.music_mode = "on"
        ctx.music_owner = "session-abc"
        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {
            "id": "next-1",
            "owner_id": "session-abc",
        }

        asyncio.run(_handle_music_next(msg, ws, ctx))

        assert ctx.music_changed.is_set()
        ws.send_json.assert_called_once_with(
            {"type": "music_next", "id": "next-1", "status": "generating"}
        )

    def test_ignored_when_music_off(self) -> None:
        ctx = _make_ctx()
        ctx.music_mode = "off"
        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {
            "id": "next-2",
            "owner_id": "session-abc",
        }

        asyncio.run(_handle_music_next(msg, ws, ctx))

        assert not ctx.music_changed.is_set()
        ws.send_json.assert_called_once_with(
            {"type": "music_next", "id": "next-2", "status": "ignored"}
        )

    def test_clears_replay_flag(self) -> None:
        ctx = _make_ctx()
        ctx.music_mode = "on"
        ctx.music_owner = "session-abc"
        ctx.music_replay = True
        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {
            "id": "next-3",
            "owner_id": "session-abc",
        }

        asyncio.run(_handle_music_next(msg, ws, ctx))

        assert ctx.music_replay is False
        assert ctx.music_changed.is_set()

    def test_error_when_no_owner_id(self) -> None:
        ctx = _make_ctx()
        ctx.music_mode = "on"
        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {"id": "next-4"}

        asyncio.run(_handle_music_next(msg, ws, ctx))

        ws.send_json.assert_called_once_with(
            {"type": "error", "id": "next-4", "message": "owner_id is required"}
        )

    def test_music_next_registered(self) -> None:
        from punt_vox.voxd import _HANDLERS

        assert "music_next" in _HANDLERS
        assert _HANDLERS["music_next"] is _handle_music_next


class TestEmptyOwnerIdRejection:
    """Handlers must reject empty owner_id to prevent ownership spoofing."""

    def test_music_on_rejects_empty_owner_id(self) -> None:
        ctx = _make_ctx()
        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {"id": "empty-1", "owner_id": "", "vibe": "focused"}

        asyncio.run(_handle_music_on(msg, ws, ctx))

        ws.send_json.assert_called_once_with(
            {"type": "error", "id": "empty-1", "message": "owner_id is required"}
        )
        # State must not mutate.
        assert ctx.music_mode == "off"

    def test_music_on_rejects_missing_owner_id(self) -> None:
        ctx = _make_ctx()
        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {"id": "empty-2", "vibe": "focused"}

        asyncio.run(_handle_music_on(msg, ws, ctx))

        ws.send_json.assert_called_once_with(
            {"type": "error", "id": "empty-2", "message": "owner_id is required"}
        )
        assert ctx.music_mode == "off"

    def test_music_vibe_rejects_empty_owner_id(self) -> None:
        ctx = _make_ctx()
        ctx.music_mode = "on"
        ctx.music_owner = "real-session"
        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {
            "id": "empty-3",
            "owner_id": "",
            "vibe": "happy",
        }

        asyncio.run(_handle_music_vibe(msg, ws, ctx))

        ws.send_json.assert_called_once_with(
            {"type": "error", "id": "empty-3", "message": "owner_id is required"}
        )
        # Vibe must not change.
        assert ctx.music_vibe == ("", "")

    def test_music_vibe_rejects_missing_owner_id(self) -> None:
        ctx = _make_ctx()
        ctx.music_mode = "on"
        ctx.music_owner = "real-session"
        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {"id": "empty-4", "vibe": "happy"}

        asyncio.run(_handle_music_vibe(msg, ws, ctx))

        ws.send_json.assert_called_once_with(
            {"type": "error", "id": "empty-4", "message": "owner_id is required"}
        )
        assert ctx.music_vibe == ("", "")


class TestAutoTrackName:
    """_auto_track_name derives vibe-style-YYYYMMDD-HHMM patterns."""

    def test_with_vibe_and_style(self) -> None:
        ctx = _make_ctx()
        ctx.music_vibe = ("happy", "[warm]")
        ctx.music_style = "techno"
        name = _auto_track_name(ctx)
        # Name has vibe-style-YYYYMMDD-HHMM structure.
        assert name.startswith("happy-techno-")
        # Suffix is YYYYMMDD-HHMM: 8 digits, dash, 4 digits.
        parts = name.split("-")
        assert len(parts[-2]) == 8  # YYYYMMDD
        assert len(parts[-1]) == 4  # HHMM

    def test_no_vibe_uses_ambient(self) -> None:
        ctx = _make_ctx()
        ctx.music_vibe = ("", "")
        ctx.music_style = ""
        name = _auto_track_name(ctx)
        assert name.startswith("ambient-mix-")

    def test_no_style_uses_mix(self) -> None:
        ctx = _make_ctx()
        ctx.music_vibe = ("chill", "")
        ctx.music_style = ""
        name = _auto_track_name(ctx)
        assert name.startswith("chill-mix-")


class TestDaemonContextTrackName:
    """DaemonContext.music_track_name defaults to empty string."""

    def test_default(self) -> None:
        ctx = _make_ctx()
        assert ctx.music_track_name == ""

    def test_music_replay_default(self) -> None:
        ctx = _make_ctx()
        assert ctx.music_replay is False


class TestHandleMusicOnWithName:
    """_handle_music_on with name field for track naming and replay."""

    def test_replay_existing_track(self, tmp_path: Path) -> None:
        """When name matches an existing file, replay without generation."""
        ctx = _make_ctx()
        ws = AsyncMock()

        # Create a fake track on disk.
        music_dir = tmp_path / "music"
        music_dir.mkdir()
        track = music_dir / "my_focus.mp3"
        track.write_bytes(b"fake-music")

        msg: dict[str, object] = {
            "type": "music_on",
            "id": "req-name-1",
            "owner_id": "session-x",
            "name": "my focus",
        }

        with patch("punt_vox.voxd._monolith._music_output_dir", return_value=music_dir):
            asyncio.run(_handle_music_on(msg, ws, ctx))

        assert ctx.music_mode == "on"
        assert ctx.music_track == track
        assert ctx.music_track_name == "my_focus"
        assert ctx.music_state == "playing"
        assert ctx.music_replay is True

        resp = ws.send_json.call_args[0][0]
        assert resp["status"] == "playing"
        assert resp["name"] == "my_focus"
        assert str(track) in resp["track"]

    def test_name_not_found_generates(self, tmp_path: Path) -> None:
        """When name does not match existing file, proceed to generation."""
        ctx = _make_ctx()
        ws = AsyncMock()

        music_dir = tmp_path / "music"
        music_dir.mkdir()
        # No file exists for "new-track".

        msg: dict[str, object] = {
            "type": "music_on",
            "id": "req-name-2",
            "owner_id": "session-y",
            "name": "new track",
        }

        with patch("punt_vox.voxd._monolith._music_output_dir", return_value=music_dir):
            asyncio.run(_handle_music_on(msg, ws, ctx))

        assert ctx.music_mode == "on"
        assert ctx.music_track_name == "new_track"
        assert ctx.music_state == "generating"
        assert ctx.music_changed.is_set()

        resp = ws.send_json.call_args[0][0]
        assert resp["status"] == "generating"

    def test_no_name_clears_track_name(self) -> None:
        """When no name is given, track_name is empty (auto-naming in generation)."""
        ctx = _make_ctx()
        ws = AsyncMock()

        msg: dict[str, object] = {
            "type": "music_on",
            "id": "req-no-name",
            "owner_id": "session-z",
        }

        asyncio.run(_handle_music_on(msg, ws, ctx))

        assert ctx.music_track_name == ""
        assert ctx.music_state == "generating"

    def test_empty_slugified_name_returns_error(self) -> None:
        """Name that slugifies to empty string returns error."""
        ctx = _make_ctx()
        ws = AsyncMock()

        msg: dict[str, object] = {
            "type": "music_on",
            "id": "req-bad-name",
            "owner_id": "session-q",
            "name": "---",
        }

        asyncio.run(_handle_music_on(msg, ws, ctx))

        resp = ws.send_json.call_args[0][0]
        assert resp["type"] == "error"
        assert "invalid track name" in resp["message"]
        assert ctx.music_mode == "off"


class TestHandleMusicPlay:
    """_handle_music_play: replay saved tracks by name."""

    def test_play_existing_track(self, tmp_path: Path) -> None:
        ctx = _make_ctx()
        ws = AsyncMock()

        music_dir = tmp_path / "music"
        music_dir.mkdir()
        track = music_dir / "chill_vibes.mp3"
        track.write_bytes(b"fake-music")

        msg: dict[str, object] = {
            "type": "music_play",
            "id": "play-1",
            "name": "chill vibes",
            "owner_id": "session-a",
        }

        with patch("punt_vox.voxd._monolith._music_output_dir", return_value=music_dir):
            asyncio.run(_handle_music_play(msg, ws, ctx))

        assert ctx.music_mode == "on"
        assert ctx.music_track == track
        assert ctx.music_track_name == "chill_vibes"
        assert ctx.music_state == "playing"
        assert ctx.music_replay is True

        resp = ws.send_json.call_args[0][0]
        assert resp["type"] == "music_play"
        assert resp["status"] == "playing"
        assert resp["name"] == "chill_vibes"

    def test_play_not_found(self, tmp_path: Path) -> None:
        ctx = _make_ctx()
        ws = AsyncMock()

        music_dir = tmp_path / "music"
        music_dir.mkdir()

        msg: dict[str, object] = {
            "type": "music_play",
            "id": "play-2",
            "name": "nonexistent",
            "owner_id": "session-b",
        }

        with patch("punt_vox.voxd._monolith._music_output_dir", return_value=music_dir):
            asyncio.run(_handle_music_play(msg, ws, ctx))

        resp = ws.send_json.call_args[0][0]
        assert resp["type"] == "error"
        assert "not found" in resp["message"]

    def test_play_missing_name(self) -> None:
        ctx = _make_ctx()
        ws = AsyncMock()

        msg: dict[str, object] = {
            "type": "music_play",
            "id": "play-3",
            "owner_id": "session-c",
        }

        asyncio.run(_handle_music_play(msg, ws, ctx))

        resp = ws.send_json.call_args[0][0]
        assert resp["type"] == "error"
        assert "name is required" in resp["message"]

    def test_play_missing_owner_id(self) -> None:
        ctx = _make_ctx()
        ws = AsyncMock()

        msg: dict[str, object] = {
            "type": "music_play",
            "id": "play-4",
            "name": "test",
        }

        asyncio.run(_handle_music_play(msg, ws, ctx))

        resp = ws.send_json.call_args[0][0]
        assert resp["type"] == "error"
        assert "owner_id is required" in resp["message"]

    def test_empty_slugified_name_returns_error(self) -> None:
        """Name that slugifies to empty string returns error."""
        ctx = _make_ctx()
        ws = AsyncMock()

        msg: dict[str, object] = {
            "type": "music_play",
            "id": "play-bad",
            "name": "---",
            "owner_id": "session-q",
        }

        asyncio.run(_handle_music_play(msg, ws, ctx))

        resp = ws.send_json.call_args[0][0]
        assert resp["type"] == "error"
        assert "invalid track name" in resp["message"]


class TestHandleMusicList:
    """_handle_music_list: returns saved tracks with metadata."""

    def test_list_empty_dir(self, tmp_path: Path) -> None:
        ctx = _make_ctx()
        ws = AsyncMock()

        music_dir = tmp_path / "music"
        music_dir.mkdir()

        msg: dict[str, object] = {"type": "music_list", "id": "list-1"}

        with patch("punt_vox.voxd._monolith._music_output_dir", return_value=music_dir):
            asyncio.run(_handle_music_list(msg, ws, ctx))

        resp = ws.send_json.call_args[0][0]
        assert resp["type"] == "music_list"
        assert resp["tracks"] == []

    def test_list_with_tracks(self, tmp_path: Path) -> None:
        ctx = _make_ctx()
        ws = AsyncMock()

        music_dir = tmp_path / "music"
        music_dir.mkdir()
        (music_dir / "alpha.mp3").write_bytes(b"a" * 1024)
        (music_dir / "beta.mp3").write_bytes(b"b" * 2048)

        msg: dict[str, object] = {"type": "music_list", "id": "list-2"}

        with patch("punt_vox.voxd._monolith._music_output_dir", return_value=music_dir):
            asyncio.run(_handle_music_list(msg, ws, ctx))

        resp = ws.send_json.call_args[0][0]
        assert resp["type"] == "music_list"
        assert len(resp["tracks"]) == 2
        names = [t["name"] for t in resp["tracks"]]
        assert "alpha" in names
        assert "beta" in names
        # Each track has required metadata fields.
        for t in resp["tracks"]:
            assert "size_bytes" in t
            assert "modified" in t
            assert "path" in t

    def test_list_nonexistent_dir(self, tmp_path: Path) -> None:
        ctx = _make_ctx()
        ws = AsyncMock()

        music_dir = tmp_path / "music_missing"

        msg: dict[str, object] = {"type": "music_list", "id": "list-3"}

        with patch("punt_vox.voxd._monolith._music_output_dir", return_value=music_dir):
            asyncio.run(_handle_music_list(msg, ws, ctx))

        resp = ws.send_json.call_args[0][0]
        assert resp["type"] == "music_list"
        assert resp["tracks"] == []


class TestHandlerRegistration:
    """New handlers are registered in _HANDLERS."""

    def test_music_play_registered(self) -> None:
        from punt_vox.voxd import _HANDLERS

        assert "music_play" in _HANDLERS
        assert _HANDLERS["music_play"] is _handle_music_play

    def test_music_list_registered(self) -> None:
        from punt_vox.voxd import _HANDLERS

        assert "music_list" in _HANDLERS
        assert _HANDLERS["music_list"] is _handle_music_list
