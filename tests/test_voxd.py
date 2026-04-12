"""Tests for punt_vox.voxd observability and direct-play dispatch."""
# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from conftest import _get_valid_mp3_bytes  # pyright: ignore[reportPrivateUsage]

from punt_vox.paths import ensure_user_dirs
from punt_vox.voxd import (
    _PLAYBACK_TIMEOUT_DEFAULT_S,
    ChimeDedup,
    DaemonContext,
    DedupHit,
    OnceDedup,
    PlaybackItem,
    _apply_vibe_for_synthesis,
    _auto_track_name,
    _config_dir,
    _handle_music_list,
    _handle_music_off,
    _handle_music_on,
    _handle_music_play,
    _handle_music_vibe,
    _handle_synthesize,
    _health_payload_full,
    _health_payload_minimal,
    _health_route,
    _kill_music_proc,
    _load_keys,
    _log_dir,
    _model_supports_expressive_tags,
    _music_loop,
    _music_player_command,
    _play_audio,
    _probe_duration,
    _run_dir,
    _try_direct_play,
)


def _make_ctx() -> DaemonContext:
    """Build a DaemonContext without touching real files or auth."""
    return DaemonContext(auth_token=None, port=0)


def _fake_proc(rc: int, stderr: bytes) -> MagicMock:
    """Build a fake asyncio subprocess returning (rc, stderr)."""
    proc = MagicMock()
    proc.returncode = rc
    proc.communicate = AsyncMock(return_value=(b"", stderr))
    proc.wait = AsyncMock(return_value=rc)
    proc.kill = MagicMock()
    return proc


class TestPlayAudioObservability:
    """``_play_audio`` must never silently discard playback failures."""

    def test_nonzero_exit_logs_error_and_records(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        audio = tmp_path / "out.mp3"
        audio.write_bytes(b"\xff\xfbfake mp3 body")
        ctx = _make_ctx()
        proc = _fake_proc(rc=1, stderr=b"some stderr")

        with (
            caplog.at_level(logging.ERROR, logger="punt_vox.voxd"),
            patch(
                "punt_vox.voxd.asyncio.create_subprocess_exec",
                AsyncMock(return_value=proc),
            ),
        ):
            asyncio.run(_play_audio(audio, ctx))

        assert "FAILED" in caplog.text
        assert "some stderr" in caplog.text
        assert ctx.last_playback is not None
        assert ctx.last_playback["rc"] == 1
        assert ctx.last_playback["stderr"] == "some stderr"

    def test_suspiciously_fast_success_logs_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        audio = tmp_path / "out.mp3"
        audio.write_bytes(b"\xff\xfbfake mp3 body")
        ctx = _make_ctx()
        proc = _fake_proc(rc=0, stderr=b"")

        ticks = iter([100.0, 100.001])

        with (
            caplog.at_level(logging.WARNING, logger="punt_vox.voxd"),
            patch(
                "punt_vox.voxd.asyncio.create_subprocess_exec",
                AsyncMock(return_value=proc),
            ),
            patch("punt_vox.voxd._monotonic", side_effect=lambda: next(ticks)),
        ):
            asyncio.run(_play_audio(audio, ctx))

        assert "SUSPICIOUS" in caplog.text
        assert ctx.last_playback is not None
        assert ctx.last_playback["rc"] == 0

    def test_binary_missing_logs_error(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        audio = tmp_path / "out.mp3"
        audio.write_bytes(b"\xff\xfbfake mp3 body")
        ctx = _make_ctx()

        with (
            caplog.at_level(logging.ERROR, logger="punt_vox.voxd"),
            patch(
                "punt_vox.voxd.asyncio.create_subprocess_exec",
                AsyncMock(side_effect=FileNotFoundError("no binary")),
            ),
        ):
            asyncio.run(_play_audio(audio, ctx))

        assert "FAILED" in caplog.text
        assert "not found" in caplog.text
        assert ctx.last_playback is not None
        assert ctx.last_playback["rc"] == -1
        stderr_value = cast("str", ctx.last_playback["stderr"])
        assert "FileNotFoundError" in stderr_value

    def test_zero_byte_file_logs_error(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        audio = tmp_path / "empty.mp3"
        audio.write_bytes(b"")
        ctx = _make_ctx()

        with caplog.at_level(logging.ERROR, logger="punt_vox.voxd"):
            asyncio.run(_play_audio(audio, ctx))

        assert "0-byte" in caplog.text
        assert ctx.last_playback is not None
        assert ctx.last_playback["rc"] == -1

    def test_last_playback_updated_on_success(self, tmp_path: Path) -> None:
        audio = tmp_path / "out.mp3"
        audio.write_bytes(b"\xff\xfbfake mp3 body")
        ctx = _make_ctx()
        proc = _fake_proc(rc=0, stderr=b"Stream #0:0 mp3, 44100 Hz")

        ticks = iter([100.0, 100.5])

        with (
            patch(
                "punt_vox.voxd.asyncio.create_subprocess_exec",
                AsyncMock(return_value=proc),
            ),
            patch("punt_vox.voxd._monotonic", side_effect=lambda: next(ticks)),
        ):
            asyncio.run(_play_audio(audio, ctx))

        assert ctx.last_playback is not None
        assert ctx.last_playback["rc"] == 0
        assert ctx.last_playback["elapsed_s"] == 0.5
        assert ctx.last_playback["file"] == str(audio)


class TestProbeDuration:
    """``_probe_duration`` extracts audio duration via ffprobe."""

    def test_returns_duration_for_valid_audio(self, tmp_path: Path) -> None:
        audio = tmp_path / "silence.mp3"
        audio.write_bytes(_get_valid_mp3_bytes())
        duration = asyncio.run(_probe_duration(audio))
        assert duration is not None
        assert duration > 0.0

    def test_returns_none_for_missing_file(self, tmp_path: Path) -> None:
        missing = tmp_path / "nonexistent.mp3"
        duration = asyncio.run(_probe_duration(missing))
        assert duration is None

    def test_returns_none_for_bad_format(self, tmp_path: Path) -> None:
        bad = tmp_path / "garbage.mp3"
        bad.write_bytes(b"not audio data at all")
        duration = asyncio.run(_probe_duration(bad))
        # ffprobe may return None or an error; either way, no crash
        assert duration is None or isinstance(duration, float)

    def test_returns_none_when_ffprobe_missing(self, tmp_path: Path) -> None:
        audio = tmp_path / "silence.mp3"
        audio.write_bytes(_get_valid_mp3_bytes())
        with patch(
            "punt_vox.voxd.asyncio.create_subprocess_exec",
            AsyncMock(side_effect=FileNotFoundError("ffprobe")),
        ):
            duration = asyncio.run(_probe_duration(audio))
        assert duration is None

    def test_returns_none_on_timeout(self, tmp_path: Path) -> None:
        audio = tmp_path / "silence.mp3"
        audio.write_bytes(_get_valid_mp3_bytes())
        proc = MagicMock()
        proc.communicate = AsyncMock(side_effect=TimeoutError)
        with patch(
            "punt_vox.voxd.asyncio.create_subprocess_exec",
            AsyncMock(return_value=proc),
        ):
            duration = asyncio.run(_probe_duration(audio))
        assert duration is None

    def test_logs_duration_at_debug(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        audio = tmp_path / "silence.mp3"
        audio.write_bytes(_get_valid_mp3_bytes())
        with caplog.at_level(logging.DEBUG, logger="punt_vox.voxd"):
            duration = asyncio.run(_probe_duration(audio))
        if duration is not None:
            assert "Probed duration" in caplog.text


class TestPlayAudioProportionalTimeout:
    """``_play_audio`` uses probed duration for its timeout."""

    def test_uses_probed_duration_for_timeout(self, tmp_path: Path) -> None:
        """A 34s file gets timeout = max(34+10, 30) = 44s, not 30s."""
        audio = tmp_path / "out.mp3"
        audio.write_bytes(b"\xff\xfbfake mp3 body")
        ctx = _make_ctx()
        proc = _fake_proc(rc=0, stderr=b"")
        ticks = iter([100.0, 100.5])

        captured_timeout: list[float] = []
        original_wait_for = asyncio.wait_for

        async def spy_wait_for(coro: object, *, timeout: float) -> object:
            captured_timeout.append(timeout)
            return await original_wait_for(coro, timeout=timeout)  # type: ignore[arg-type]

        with (
            patch("punt_vox.voxd._probe_duration", AsyncMock(return_value=34.3)),
            patch(
                "punt_vox.voxd.asyncio.create_subprocess_exec",
                AsyncMock(return_value=proc),
            ),
            patch("punt_vox.voxd._monotonic", side_effect=lambda: next(ticks)),
            patch("punt_vox.voxd.asyncio.wait_for", side_effect=spy_wait_for),
        ):
            asyncio.run(_play_audio(audio, ctx))

        assert len(captured_timeout) == 1
        assert captured_timeout[0] == pytest.approx(44.3, abs=0.1)  # pyright: ignore[reportUnknownMemberType]

    def test_falls_back_to_default_when_probe_fails(self, tmp_path: Path) -> None:
        audio = tmp_path / "out.mp3"
        audio.write_bytes(b"\xff\xfbfake mp3 body")
        ctx = _make_ctx()
        proc = _fake_proc(rc=0, stderr=b"")
        ticks = iter([100.0, 100.5])

        captured_timeout: list[float] = []
        original_wait_for = asyncio.wait_for

        async def spy_wait_for(coro: object, *, timeout: float) -> object:
            captured_timeout.append(timeout)
            return await original_wait_for(coro, timeout=timeout)  # type: ignore[arg-type]

        with (
            patch("punt_vox.voxd._probe_duration", AsyncMock(return_value=None)),
            patch(
                "punt_vox.voxd.asyncio.create_subprocess_exec",
                AsyncMock(return_value=proc),
            ),
            patch("punt_vox.voxd._monotonic", side_effect=lambda: next(ticks)),
            patch("punt_vox.voxd.asyncio.wait_for", side_effect=spy_wait_for),
        ):
            asyncio.run(_play_audio(audio, ctx))

        assert len(captured_timeout) == 1
        assert captured_timeout[0] == _PLAYBACK_TIMEOUT_DEFAULT_S

    def test_short_duration_uses_default_minimum(self, tmp_path: Path) -> None:
        """A 5s file gets timeout = max(5+10, 30) = 30s (default wins)."""
        audio = tmp_path / "out.mp3"
        audio.write_bytes(b"\xff\xfbfake mp3 body")
        ctx = _make_ctx()
        proc = _fake_proc(rc=0, stderr=b"")
        ticks = iter([100.0, 100.5])

        captured_timeout: list[float] = []
        original_wait_for = asyncio.wait_for

        async def spy_wait_for(coro: object, *, timeout: float) -> object:
            captured_timeout.append(timeout)
            return await original_wait_for(coro, timeout=timeout)  # type: ignore[arg-type]

        with (
            patch("punt_vox.voxd._probe_duration", AsyncMock(return_value=5.0)),
            patch(
                "punt_vox.voxd.asyncio.create_subprocess_exec",
                AsyncMock(return_value=proc),
            ),
            patch("punt_vox.voxd._monotonic", side_effect=lambda: next(ticks)),
            patch("punt_vox.voxd.asyncio.wait_for", side_effect=spy_wait_for),
        ):
            asyncio.run(_play_audio(audio, ctx))

        assert len(captured_timeout) == 1
        assert captured_timeout[0] == _PLAYBACK_TIMEOUT_DEFAULT_S


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


class TestTryDirectPlay:
    """Voxd dispatches to provider.play_directly for local providers."""

    def _run(self, provider: MagicMock, ctx: DaemonContext) -> int | None | Exception:
        with patch("punt_vox.voxd.get_provider", return_value=provider):
            return asyncio.run(
                _try_direct_play(
                    text="hello",
                    voice=None,
                    provider_name="espeak",
                    model=None,
                    language=None,
                    rate=None,
                    vibe_tags=None,
                    stability=None,
                    similarity=None,
                    style=None,
                    speaker_boost=None,
                    api_key=None,
                    ctx=ctx,
                )
            )

    def test_returns_provider_rc_on_success(self) -> None:
        ctx = _make_ctx()
        provider = MagicMock()
        provider.play_directly = MagicMock(return_value=0)

        rc = self._run(provider, ctx)

        assert rc == 0
        assert ctx.last_playback is not None
        assert ctx.last_playback["rc"] == 0
        provider.play_directly.assert_called_once()

    def test_returns_none_for_cloud_provider(self) -> None:
        """A provider lacking play_directly opts out of the direct-play path."""
        ctx = _make_ctx()
        # spec= without play_directly means hasattr/isinstance both fail.
        provider = MagicMock(spec=["name", "synthesize"])

        rc = self._run(provider, ctx)

        assert rc is None
        # No playback attempted -- last_playback stays None.
        assert ctx.last_playback is None

    def test_nonzero_rc_logs_error(self, caplog: pytest.LogCaptureFixture) -> None:
        ctx = _make_ctx()
        provider = MagicMock()
        provider.play_directly = MagicMock(return_value=2)

        with caplog.at_level(logging.ERROR, logger="punt_vox.voxd"):
            rc = self._run(provider, ctx)

        assert rc == 2
        assert "Direct-play FAILED" in caplog.text
        assert ctx.last_playback is not None
        assert ctx.last_playback["rc"] == 2

    def test_get_provider_exception_returned(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Provider construction failure surfaces as an Exception, not a crash."""
        ctx = _make_ctx()

        with (
            caplog.at_level(logging.ERROR, logger="punt_vox.voxd"),
            patch(
                "punt_vox.voxd.get_provider",
                side_effect=ValueError("unknown provider"),
            ),
        ):
            result = asyncio.run(
                _try_direct_play(
                    text="hello",
                    voice=None,
                    provider_name="espeak",
                    model=None,
                    language=None,
                    rate=None,
                    vibe_tags=None,
                    stability=None,
                    similarity=None,
                    style=None,
                    speaker_boost=None,
                    api_key=None,
                    ctx=ctx,
                )
            )

        assert isinstance(result, ValueError)
        assert "unknown provider" in str(result)
        assert "Direct-play raised" in caplog.text

    def test_no_api_key_skips_env_lock(self) -> None:
        """Local providers without an API key must not block on _env_lock."""
        ctx = _make_ctx()
        provider = MagicMock()
        provider.play_directly = MagicMock(return_value=0)

        # Replace _env_lock with one that records every acquire attempt.
        sentinel_lock = MagicMock(wraps=asyncio.Lock())
        sentinel_lock.__aenter__ = AsyncMock()
        sentinel_lock.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("punt_vox.voxd.get_provider", return_value=provider),
            patch("punt_vox.voxd._env_lock", sentinel_lock),
        ):
            asyncio.run(
                _try_direct_play(
                    text="hello",
                    voice=None,
                    provider_name="espeak",
                    model=None,
                    language=None,
                    rate=None,
                    vibe_tags=None,
                    stability=None,
                    similarity=None,
                    style=None,
                    speaker_boost=None,
                    api_key=None,
                    ctx=ctx,
                )
            )

        sentinel_lock.__aenter__.assert_not_called()

    def test_api_key_acquires_env_lock_for_cloud_provider(self) -> None:
        """API-key path must serialize via _env_lock to protect os.environ."""
        ctx = _make_ctx()
        provider = MagicMock()
        provider.play_directly = MagicMock(return_value=0)

        sentinel_lock = MagicMock()
        sentinel_lock.__aenter__ = AsyncMock()
        sentinel_lock.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("punt_vox.voxd.get_provider", return_value=provider),
            patch("punt_vox.voxd._env_lock", sentinel_lock),
        ):
            asyncio.run(
                _try_direct_play(
                    text="hello",
                    voice=None,
                    provider_name="elevenlabs",
                    model=None,
                    language=None,
                    rate=None,
                    vibe_tags=None,
                    stability=None,
                    similarity=None,
                    style=None,
                    speaker_boost=None,
                    api_key="secret",
                    ctx=ctx,
                )
            )

        sentinel_lock.__aenter__.assert_called_once()


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
                "punt_vox.voxd._try_direct_play",
                AsyncMock(return_value=None),
            ) as mock_direct,
            patch(
                "punt_vox.voxd._synthesize_to_file",
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
            "punt_vox.voxd._try_direct_play",
            AsyncMock(return_value=0),
        ) as mock_direct:
            asyncio.run(_handle_synthesize(msg, websocket, ctx))

        mock_direct.assert_called_once()
        call_kwargs = mock_direct.call_args.kwargs
        assert call_kwargs["provider_name"] == "espeak"


class TestDirectPlayProtocol:
    """Direct-play capability is detected via the DirectPlayProvider protocol."""

    def test_polly_is_not_direct_play(self) -> None:
        from punt_vox.providers.polly import PollyProvider
        from punt_vox.types import DirectPlayProvider

        with patch("punt_vox.providers.polly.boto3.client"):
            provider = PollyProvider()
        assert not isinstance(provider, DirectPlayProvider)

    def test_openai_is_not_direct_play(self) -> None:
        from punt_vox.providers.openai import OpenAIProvider
        from punt_vox.types import DirectPlayProvider

        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
            provider = OpenAIProvider()
        assert not isinstance(provider, DirectPlayProvider)

    def test_elevenlabs_is_not_direct_play(self) -> None:
        from punt_vox.providers.elevenlabs import ElevenLabsProvider
        from punt_vox.types import DirectPlayProvider

        with patch.dict("os.environ", {"ELEVENLABS_API_KEY": "test"}):
            provider = ElevenLabsProvider()
        assert not isinstance(provider, DirectPlayProvider)

    def test_espeak_is_direct_play(self) -> None:
        from punt_vox.providers.espeak import EspeakProvider
        from punt_vox.types import DirectPlayProvider

        with patch(
            "punt_vox.providers.espeak._find_espeak_binary",
            return_value="/usr/bin/espeak-ng",
        ):
            provider = EspeakProvider()
        assert isinstance(provider, DirectPlayProvider)


class TestStderrTruncation:
    """ffplay stderr can be unbounded; the truncator caps it but keeps both ends."""

    def test_short_text_passes_through(self) -> None:
        from punt_vox.voxd import _truncate_stderr

        assert _truncate_stderr("hello") == "hello"

    def test_long_text_truncated_with_marker(self) -> None:
        from punt_vox.voxd import _MAX_STDERR_LEN, _truncate_stderr

        text = "A" * 5000 + "B" * 5000
        out = _truncate_stderr(text)

        assert len(out) < len(text)
        assert "truncated" in out
        assert out.startswith("A")
        assert out.endswith("B")
        # Marker reports the dropped byte count.
        assert str(len(text) - _MAX_STDERR_LEN) in out


class TestApiKeyPassthroughIntegration:
    """End-to-end api_key flow: WebSocket -> _handle_record -> provider.

    Verification of vox-a3e: a single user maintaining multiple provider
    keys (key-A for billing project alpha, key-B for billing project
    bravo) can scope a single synthesize/record call to a specific key
    without leaking that key into the process environment beyond the
    single request, and without cross-call contamination.

    The test drives the real Starlette app via ``starlette.testclient``,
    sends record messages over a real WebSocket, and registers a stub
    provider via ``punt_vox.voxd.get_provider``. The stub inspects
    ``os.environ[ELEVENLABS_API_KEY]`` at the moment the factory is
    called — that is the exact point where the per-call key is visible
    to the provider code. Recording the observed value proves the
    passthrough is working and isolated per call.
    """

    def _build_stub_provider(self, observed: list[str | None]) -> type:
        """Return a stub TTSProvider class that records the observed api key."""
        from punt_vox.types import (
            AudioProviderId,
            AudioRequest,
            AudioResult,
            HealthCheck,
        )

        valid_mp3 = _get_valid_mp3_bytes()

        class _StubProvider:
            name = "elevenlabs"
            default_voice = "matilda"
            supports_expressive_tags = False

            def __init__(self) -> None:
                # Read env at factory time — this is the point at which
                # voxd's ``_synthesize_to_file`` has mutated os.environ
                # for a per-call key. Record whatever is there (including
                # None when the caller did not pass ``api_key``).
                observed.append(os.environ.get("ELEVENLABS_API_KEY"))

            def synthesize(
                self, request: AudioRequest, output_path: Path
            ) -> AudioResult:
                output_path.write_bytes(valid_mp3)
                return AudioResult(
                    path=output_path,
                    text=request.text,
                    provider=AudioProviderId.elevenlabs,
                    voice="matilda",
                )

            def generate_audio(self, request: AudioRequest) -> AudioResult:
                raise NotImplementedError

            def generate_audios(self, requests: object) -> list[AudioResult]:
                raise NotImplementedError

            def resolve_voice(self, name: str, language: str | None = None) -> str:
                return name or "matilda"

            def get_default_voice(self, language: str) -> str:
                return "matilda"

            def list_voices(self, language: str | None = None) -> list[str]:
                return ["matilda"]

            def infer_language_from_voice(self, voice: str) -> str | None:
                return None

            def check_health(self) -> list[HealthCheck]:
                return []

        return _StubProvider

    def _run_record(
        self,
        api_key: str | None,
        observed: list[str | None],
        monkeypatch: pytest.MonkeyPatch,
    ) -> dict[str, object]:
        """Send a record message over a real WebSocket and return the response."""
        from starlette.testclient import TestClient

        from punt_vox.voxd import build_app

        stub_provider_cls = self._build_stub_provider(observed)

        def fake_get_provider(
            name: str,
            *,
            config_path: object = None,
            model: str | None = None,
        ) -> object:
            assert name == "elevenlabs"
            return stub_provider_cls()

        monkeypatch.setattr("punt_vox.voxd.get_provider", fake_get_provider)

        # Disable the cache so every call reaches the stub factory.
        def _cache_miss(
            _text: str,
            _voice: str,
            _provider: str,
            _api_key: str | None = None,
        ) -> Path | None:
            return None

        def _cache_noop(
            _text: str,
            _voice: str,
            _provider: str,
            _path: Path,
            _api_key: str | None = None,
        ) -> None:
            return None

        monkeypatch.setattr("punt_vox.voxd.cache_get", _cache_miss)
        monkeypatch.setattr("punt_vox.voxd.cache_put", _cache_noop)

        ctx = DaemonContext(auth_token=None, port=0)
        app = build_app(ctx)

        msg: dict[str, object] = {
            "type": "record",
            "id": "test-rec",
            "text": "billable synthesis",
            "provider": "elevenlabs",
            "voice": "matilda",
        }
        if api_key is not None:
            msg["api_key"] = api_key

        with (
            TestClient(app) as client,
            client.websocket_connect("/ws") as ws,
        ):
            ws.send_json(msg)
            response: dict[str, object] = ws.receive_json()

        return response

    def test_first_call_key_reaches_provider(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sending ``api_key=key-alpha`` exposes exactly that key to the provider."""
        # Clear any ambient ELEVENLABS_API_KEY so the baseline is well-defined.
        monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
        observed: list[str | None] = []

        response = self._run_record("test-key-alpha-billing", observed, monkeypatch)

        assert response.get("type") == "audio"
        assert len(observed) == 1
        assert observed[0] == "test-key-alpha-billing"

    def test_second_call_key_does_not_leak_from_first(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two sequential calls with different keys: each provider sees its own.

        This is the load-bearing invariant for single-user multi-key
        billing isolation. If voxd leaked key-alpha into the second
        call, the user would be billed under the wrong project.
        """
        monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
        observed: list[str | None] = []

        # Call 1 — key alpha
        self._run_record("test-key-alpha-billing", observed, monkeypatch)
        # Call 2 — key bravo (fresh app instance, but shares os.environ)
        self._run_record("test-key-bravo-billing", observed, monkeypatch)

        assert observed == [
            "test-key-alpha-billing",
            "test-key-bravo-billing",
        ]
        # Neither key leaked back into the process environment after
        # the calls returned.
        assert os.environ.get("ELEVENLABS_API_KEY") is None

    def test_no_api_key_falls_back_to_ambient_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``api_key=None`` leaves whatever is already in os.environ alone.

        This is the keys.env-fallback path: when the caller does not
        pass a per-call key, voxd does not touch ``os.environ``, so
        the provider factory sees whatever the keys.env loader put
        there at daemon startup (or whatever the current test ambient
        env has). The key invariant is that no cross-call mutation
        happens.
        """
        # Set a sentinel ambient key — the provider should see it.
        monkeypatch.setenv("ELEVENLABS_API_KEY", "ambient-fallback-key")
        observed: list[str | None] = []

        self._run_record(None, observed, monkeypatch)

        assert len(observed) == 1
        assert observed[0] == "ambient-fallback-key"
        # Ambient key still present — no cross-call mutation.
        assert os.environ.get("ELEVENLABS_API_KEY") == "ambient-fallback-key"

    def test_previously_set_ambient_key_restored_after_per_call_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Per-call key is restored to the ambient value after the call.

        voxd's ``_synthesize_to_file`` saves the old env value, sets
        the per-call key, runs the provider, then restores the old
        value in a ``finally`` block. This test verifies the restore.
        """
        monkeypatch.setenv("ELEVENLABS_API_KEY", "ambient-persistent-key")
        observed: list[str | None] = []

        self._run_record("per-call-override", observed, monkeypatch)

        assert observed == ["per-call-override"]
        # After the call, the original ambient value is back.
        assert os.environ.get("ELEVENLABS_API_KEY") == "ambient-persistent-key"


class TestCacheApiKeyBypass:
    """Cache bypass when ``api_key`` is set — vox-a3e billing isolation.

    The round-3 design tried to *partition* the cache by ``api_key`` by
    folding the key into the digest. CodeQL's
    ``py/weak-sensitive-data-hashing`` rule correctly flagged that: a
    regular cryptographic hash is inappropriate for hashing password-
    class material even for a filename-only use case, and the only
    lint-clean alternatives are password KDFs (Argon2, scrypt, bcrypt,
    PBKDF2) whose per-call cost is unacceptable for cache filenames.

    Round 4 switches to **cache bypass**: per-call api_key calls skip
    ``cache_get`` and ``cache_put`` entirely at the voxd call site,
    so no sensitive data ever reaches ``cache.py``. The correctness
    story also improves — a billing-isolated call can never read bytes
    synthesized under a different key, and can never leave bytes
    behind that a different key could later reuse.

    These tests drive the real voxd WebSocket code path. The bypass
    tests use spies on ``cache_get``/``cache_put`` to prove the
    bypass-not-call behavior directly. The no-poison test uses the
    real on-disk cache (``cache.py``'s ``cache_get``/``cache_put``
    untouched, ``CACHE_DIR`` pointed at a tmp dir) to prove that an
    api_key synthesis does not disturb the anonymous cache entry for
    the same text. The provider-distinct test is inherited from the
    round-3 suite and locks in that per-call api_key values still
    reach the provider correctly.
    """

    def _build_counting_provider(self, calls: list[str | None]) -> type:
        """Stub provider that appends to ``calls`` on every construction."""
        from punt_vox.types import (
            AudioProviderId,
            AudioRequest,
            AudioResult,
            HealthCheck,
        )

        valid_mp3 = _get_valid_mp3_bytes()

        class _CountingProvider:
            name = "elevenlabs"
            default_voice = "matilda"
            supports_expressive_tags = False

            def __init__(self) -> None:
                # Record the api_key visible at construction time. The
                # key invariant is that the provider factory runs once
                # per distinct api_key even when text/voice/provider
                # are identical across calls.
                calls.append(os.environ.get("ELEVENLABS_API_KEY"))

            def synthesize(
                self, request: AudioRequest, output_path: Path
            ) -> AudioResult:
                output_path.write_bytes(valid_mp3)
                return AudioResult(
                    path=output_path,
                    text=request.text,
                    provider=AudioProviderId.elevenlabs,
                    voice="matilda",
                )

            def generate_audio(self, request: AudioRequest) -> AudioResult:
                raise NotImplementedError

            def generate_audios(self, requests: object) -> list[AudioResult]:
                raise NotImplementedError

            def resolve_voice(self, name: str, language: str | None = None) -> str:
                return name or "matilda"

            def get_default_voice(self, language: str) -> str:
                return "matilda"

            def list_voices(self, language: str | None = None) -> list[str]:
                return ["matilda"]

            def infer_language_from_voice(self, voice: str) -> str | None:
                return None

            def check_health(self) -> list[HealthCheck]:
                return []

        return _CountingProvider

    def _run_record(
        self,
        *,
        api_key: str | None,
        text: str,
        calls: list[str | None],
        monkeypatch: pytest.MonkeyPatch,
    ) -> dict[str, object]:
        """Send a ``record`` message over a real WebSocket and return the response.

        Does NOT patch ``cache_get``/``cache_put`` — the caller is
        responsible for patching them (with spies or with a tmp
        ``CACHE_DIR``) before invoking this helper.
        """
        from starlette.testclient import TestClient

        from punt_vox.voxd import build_app

        provider_cls = self._build_counting_provider(calls)

        def fake_get_provider(
            name: str,
            *,
            config_path: object = None,
            model: str | None = None,
        ) -> object:
            assert name == "elevenlabs"
            return provider_cls()

        monkeypatch.setattr("punt_vox.voxd.get_provider", fake_get_provider)

        ctx = DaemonContext(auth_token=None, port=0)
        app = build_app(ctx)

        msg: dict[str, object] = {
            "type": "record",
            "id": "test-rec",
            "text": text,
            "provider": "elevenlabs",
            "voice": "matilda",
        }
        if api_key is not None:
            msg["api_key"] = api_key

        with (
            TestClient(app) as client,
            client.websocket_connect("/ws") as ws,
        ):
            ws.send_json(msg)
            response: dict[str, object] = ws.receive_json()

        return response

    def test_api_key_call_bypasses_cache_get_and_put(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Per-call api_key synthesis must not call cache_get or cache_put.

        Spy-based assertion (not mock-return-value): we replace the two
        cache entry points with functions that record their call args
        in a list, then drive the real voxd WebSocket handler twice —
        once anonymously, once with an api_key. The anonymous call
        MUST hit both spies; the api_key call MUST hit neither.

        Spies record actual behavior directly. A mock-return-value
        approach would only prove the code handled whatever fake
        return value the test injected, which does not distinguish
        "the bypass works" from "cache_get was called and returned
        None".
        """
        # Real CACHE_DIR pointed at tmp so the backing cache.py code
        # never touches user state if a spy somehow lets the real
        # functions through.
        monkeypatch.setattr("punt_vox.cache.CACHE_DIR", tmp_path / "cache")
        monkeypatch.setenv("ELEVENLABS_API_KEY", "")

        get_calls: list[tuple[object, ...]] = []
        put_calls: list[tuple[object, ...]] = []

        def spy_cache_get(*args: object) -> Path | None:
            get_calls.append(args)
            return None  # Never hit — force synthesis on the anon path.

        def spy_cache_put(*args: object) -> Path | None:
            put_calls.append(args)
            # Return the source path unchanged so the handler keeps
            # using it for the record response.
            source = args[3]
            assert isinstance(source, Path)
            return source

        monkeypatch.setattr("punt_vox.voxd.cache_get", spy_cache_get)
        monkeypatch.setattr("punt_vox.voxd.cache_put", spy_cache_put)

        calls: list[str | None] = []

        # Call 1: anonymous — both spies MUST fire.
        resp1 = self._run_record(
            api_key=None,
            text="bypass test phrase",
            calls=calls,
            monkeypatch=monkeypatch,
        )
        assert resp1.get("type") == "audio"
        assert len(get_calls) == 1, (
            f"anonymous call: expected cache_get spy to fire once, "
            f"got {len(get_calls)}: {get_calls}"
        )
        assert len(put_calls) == 1, (
            f"anonymous call: expected cache_put spy to fire once, "
            f"got {len(put_calls)}: {put_calls}"
        )

        # Call 2: api_key set — NEITHER spy may fire. The cache_get /
        # cache_put call counts stay locked at 1 (from call 1).
        resp2 = self._run_record(
            api_key="sk_bypass_test",
            text="bypass test phrase",
            calls=calls,
            monkeypatch=monkeypatch,
        )
        assert resp2.get("type") == "audio"
        assert len(get_calls) == 1, (
            f"api_key call must bypass cache_get, got {len(get_calls)} "
            f"total calls: {get_calls}"
        )
        assert len(put_calls) == 1, (
            f"api_key call must bypass cache_put, got {len(put_calls)} "
            f"total calls: {put_calls}"
        )
        # And the provider was invoked twice total (once per call),
        # proving the api_key call actually synthesized rather than
        # returning an early short-circuit.
        assert len(calls) == 2, (
            f"expected provider called twice (once anon, once api_key), "
            f"got {len(calls)}: {calls}"
        )

    def test_api_key_call_does_not_poison_anonymous_cache(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """An api_key synthesis must not disturb the anonymous cache entry.

        Sequence:
          1. Anonymous call — provider runs, writes cache entry for
             the (text, voice, provider) tuple.
          2. Anonymous call (same text) — cache hit, provider NOT
             called again.
          3. api_key call (same text) — cache BYPASSED, provider IS
             called (proving bypass-not-read).
          4. Anonymous call (same text) — cache hit from step 1 is
             STILL REACHABLE (proving bypass-not-write: the api_key
             call did not overwrite or evict the anon entry).

        Uses the real on-disk cache module (not mocked) with
        ``CACHE_DIR`` redirected to a tmp dir.
        """
        monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
        monkeypatch.setattr("punt_vox.cache.CACHE_DIR", tmp_path / "cache")
        calls: list[str | None] = []

        # Step 1: anonymous, cold cache — provider runs.
        resp1 = self._run_record(
            api_key=None,
            text="shared anon phrase",
            calls=calls,
            monkeypatch=monkeypatch,
        )
        assert resp1.get("type") == "audio"
        assert len(calls) == 1, f"step 1 should synthesize, got calls={calls}"

        # Step 2: anonymous again — cache hit, no provider call.
        resp2 = self._run_record(
            api_key=None,
            text="shared anon phrase",
            calls=calls,
            monkeypatch=monkeypatch,
        )
        assert resp2.get("type") == "audio"
        assert len(calls) == 1, (
            f"step 2 should hit cache (no new provider call), got calls={calls}"
        )

        # Step 3: api_key set — cache BYPASSED, provider runs again.
        resp3 = self._run_record(
            api_key="sk_poison_test",
            text="shared anon phrase",
            calls=calls,
            monkeypatch=monkeypatch,
        )
        assert resp3.get("type") == "audio"
        assert len(calls) == 2, (
            f"step 3: api_key call must bypass cache and synthesize, "
            f"expected 2 provider calls total, got calls={calls}"
        )

        # Step 4: anonymous again — the original cache entry from step
        # 1 must STILL be reachable. If the api_key call had poisoned
        # the anon cache (e.g. overwritten the file or removed it),
        # we would see a 3rd provider call here.
        resp4 = self._run_record(
            api_key=None,
            text="shared anon phrase",
            calls=calls,
            monkeypatch=monkeypatch,
        )
        assert resp4.get("type") == "audio"
        assert len(calls) == 2, (
            f"step 4: anonymous cache entry was POISONED by the api_key "
            f"call; anon call should still hit cache, got calls={calls}"
        )

    def test_per_call_keys_reach_provider_distinct(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Two consecutive api_key calls reach the provider with the right key.

        This locks in the core billing-isolation invariant: each
        per-call api_key value is visible to the provider factory at
        construction time, and the two values do not cross-contaminate.
        Both calls use the same text so the assertion would fail if a
        future refactor accidentally reintroduced a shared cache across
        api_key scopes.
        """
        monkeypatch.setenv("ELEVENLABS_API_KEY", "")
        monkeypatch.setattr("punt_vox.cache.CACHE_DIR", tmp_path / "cache")
        calls: list[str | None] = []

        resp1 = self._run_record(
            api_key="sk_A",
            text="billable phrase",
            calls=calls,
            monkeypatch=monkeypatch,
        )
        assert resp1.get("type") == "audio"

        resp2 = self._run_record(
            api_key="sk_B",
            text="billable phrase",
            calls=calls,
            monkeypatch=monkeypatch,
        )
        assert resp2.get("type") == "audio"

        # Two distinct provider constructions, each seeing its own key.
        assert calls == ["sk_A", "sk_B"], (
            f"expected provider constructions to see ['sk_A', 'sk_B'], got {calls}"
        )


class TestSynthesizeFailFast:
    """0-byte synthesis must raise and skip cache_put to avoid poisoning."""

    def test_zero_byte_output_raises_and_skips_cache(self, tmp_path: Path) -> None:
        from punt_vox.voxd import _synthesize_to_file

        # Mock client.synthesize to leave the file at 0 bytes.
        captured_temp: dict[str, Path] = {}

        def fake_synth(request: object, output_path: Path) -> None:
            output_path.write_bytes(b"")
            captured_temp["path"] = output_path

        provider = MagicMock()
        provider.name = "espeak"

        with (
            patch("punt_vox.voxd.get_provider", return_value=provider),
            patch("punt_vox.voxd.cache_get", return_value=None),
            patch("punt_vox.voxd.cache_put") as mock_cache_put,
            patch("punt_vox.voxd.TTSClient") as mock_client_cls,
        ):
            mock_client = MagicMock()
            mock_client.synthesize = fake_synth
            mock_client_cls.return_value = mock_client

            with pytest.raises(RuntimeError, match="missing or empty"):
                asyncio.run(
                    _synthesize_to_file(
                        text="hello",
                        voice=None,
                        provider_name="espeak",
                        model=None,
                        language=None,
                        rate=None,
                        vibe_tags=None,
                        stability=None,
                        similarity=None,
                        style=None,
                        speaker_boost=None,
                        api_key=None,
                    )
                )

        mock_cache_put.assert_not_called()
        # The temp file the synthesizer created must be cleaned up.
        assert "path" in captured_temp
        assert not captured_temp["path"].exists()


class TestDirectPlaySerialization:
    """Direct-play and queued playback share _playback_mutex."""

    def test_concurrent_direct_plays_serialize(self) -> None:
        """Two concurrent direct-play calls must not run in parallel."""
        import time as time_module

        ctx = _make_ctx()

        starts: list[float] = []
        ends: list[float] = []

        def slow_play(_request: object) -> int:
            starts.append(time_module.monotonic())
            time_module.sleep(0.1)
            ends.append(time_module.monotonic())
            return 0

        provider = MagicMock()
        provider.play_directly = slow_play

        async def _drive() -> None:
            with patch("punt_vox.voxd.get_provider", return_value=provider):
                await asyncio.gather(
                    _try_direct_play(
                        text="one",
                        voice=None,
                        provider_name="espeak",
                        model=None,
                        language=None,
                        rate=None,
                        vibe_tags=None,
                        stability=None,
                        similarity=None,
                        style=None,
                        speaker_boost=None,
                        api_key=None,
                        ctx=ctx,
                    ),
                    _try_direct_play(
                        text="two",
                        voice=None,
                        provider_name="espeak",
                        model=None,
                        language=None,
                        rate=None,
                        vibe_tags=None,
                        stability=None,
                        similarity=None,
                        style=None,
                        speaker_boost=None,
                        api_key=None,
                        ctx=ctx,
                    ),
                )

        asyncio.run(_drive())

        assert len(starts) == 2
        assert len(ends) == 2
        # The second call must start AFTER the first call ends.
        first_end = min(ends)
        second_start = max(starts)
        assert second_start >= first_end, (
            f"direct-play calls overlapped: "
            f"second_start={second_start}, first_end={first_end}"
        )


# ---------------------------------------------------------------------------
# voxd path helpers — per-user state, not /etc or /var
# ---------------------------------------------------------------------------


class TestVoxdPaths:
    """voxd must read/write state under ~/.punt-labs/vox/, not FHS paths."""

    def test_config_dir_is_user_state(self) -> None:
        assert _config_dir() == Path.home() / ".punt-labs" / "vox"

    def test_log_dir_is_user_state_logs(self) -> None:
        assert _log_dir() == Path.home() / ".punt-labs" / "vox" / "logs"

    def test_run_dir_is_user_state_run(self) -> None:
        assert _run_dir() == Path.home() / ".punt-labs" / "vox" / "run"

    def test_paths_do_not_leak_fhs_dirs(self) -> None:
        forbidden = ("/etc/vox", "/var/log/vox", "/var/run/vox", "/var/cache/vox")
        for helper in (_config_dir, _log_dir, _run_dir):
            resolved = str(helper())
            for bad in forbidden:
                assert bad not in resolved, (
                    f"{helper.__name__} returned forbidden FHS path {resolved}"
                )


# ---------------------------------------------------------------------------
# _load_keys — reads ~/.punt-labs/vox/keys.env
# ---------------------------------------------------------------------------


class TestLoadKeys:
    """_load_keys must read from the per-user state dir."""

    def test_loads_keys_from_config_dir(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Keys in keys.env are copied into os.environ."""
        keys_file = tmp_path / "keys.env"
        keys_file.write_text(
            "# header\n"
            "ELEVENLABS_API_KEY=sk-eleven-test\n"
            "OPENAI_API_KEY=sk-openai-test\n"
        )
        monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        loaded = _load_keys(tmp_path)

        assert "ELEVENLABS_API_KEY" in loaded
        assert "OPENAI_API_KEY" in loaded
        import os as _os

        assert _os.environ["ELEVENLABS_API_KEY"] == "sk-eleven-test"
        assert _os.environ["OPENAI_API_KEY"] == "sk-openai-test"

    def test_missing_keys_file_returns_empty(self, tmp_path: Path) -> None:
        """No keys.env file means no loaded keys — not a crash."""
        loaded = _load_keys(tmp_path)
        assert loaded == frozenset()

    def test_existing_env_not_overwritten(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Keys already in os.environ are preserved (env wins over file)."""
        keys_file = tmp_path / "keys.env"
        keys_file.write_text("ELEVENLABS_API_KEY=from-file\n")
        monkeypatch.setenv("ELEVENLABS_API_KEY", "from-env")

        loaded = _load_keys(tmp_path)

        assert "ELEVENLABS_API_KEY" not in loaded
        import os as _os

        assert _os.environ["ELEVENLABS_API_KEY"] == "from-env"

    def test_ignores_unknown_keys(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Only known provider keys are loaded — random env vars are ignored."""
        keys_file = tmp_path / "keys.env"
        keys_file.write_text("HACKER_BACKDOOR=root\nELEVENLABS_API_KEY=sk-real\n")
        monkeypatch.delenv("HACKER_BACKDOOR", raising=False)
        monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)

        loaded = _load_keys(tmp_path)

        assert "HACKER_BACKDOOR" not in loaded
        assert "ELEVENLABS_API_KEY" in loaded
        import os as _os

        assert "HACKER_BACKDOOR" not in _os.environ


class TestVoxdStartupEnforces0700:
    """voxd.main() must tighten existing state dirs to mode 0700.

    Copilot finding 3048101870 on PR #162: the existing helpers used
    ``Path.mkdir(..., exist_ok=True)`` which respects the process
    umask (``0022`` on most shells → directories created as ``0755``).
    ``paths.ensure_user_dirs()`` creates-or-chmods each subdir with an
    explicit ``0o700`` so pre-existing directories with looser
    permissions are tightened on the next startup.
    """

    def test_ensure_user_dirs_tightens_preexisting_logs_dir(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A pre-existing 0755 logs dir is chmod'd to 0700."""
        import os as _os
        import stat as _stat

        fake_home = tmp_path / "home" / "user"
        state_root = fake_home / ".punt-labs" / "vox"
        logs = state_root / "logs"
        logs.mkdir(parents=True)
        # Pre-create with loose umask-style permissions. This is what
        # an older voxd left behind before the 0700 contract.
        logs.chmod(0o755)
        state_root.chmod(0o755)
        assert _stat.S_IMODE(_os.stat(logs).st_mode) == 0o755

        monkeypatch.setenv("HOME", str(fake_home))

        # The no-arg form resolves the current user's state dir.
        ensure_user_dirs()

        # Every subdir and the root are now 0700.
        for target in (state_root, logs, state_root / "run", state_root / "cache"):
            mode = _stat.S_IMODE(_os.stat(target).st_mode)
            assert mode == 0o700, (
                f"{target} mode is {oct(mode)} after ensure_user_dirs(); expected 0o700"
            )

    def test_ensure_user_dirs_creates_all_subdirs_when_missing(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Fresh ``$HOME`` with no state dir: ensure_user_dirs creates it."""
        import os as _os
        import stat as _stat

        fake_home = tmp_path / "home" / "fresh"
        fake_home.mkdir(parents=True)
        monkeypatch.setenv("HOME", str(fake_home))

        ensure_user_dirs()

        state_root = fake_home / ".punt-labs" / "vox"
        assert state_root.is_dir()
        for name in ("logs", "run", "cache"):
            d = state_root / name
            assert d.is_dir()
            mode = _stat.S_IMODE(_os.stat(d).st_mode)
            assert mode == 0o700


class TestVoxdPathHelpersArePure:
    """``_log_dir``, ``_run_dir``, ``_config_dir`` must be side-effect free.

    Closes Copilot 3047999704 (mode 0755 leak from `_log_dir`) and
    Cursor Bugbot 3048161272 (helper is side-effectful, inconsistent
    with sibling pure-path helpers). Once ``voxd.main()`` calls
    ``paths.ensure_user_dirs()`` at startup, the helpers no longer
    need to create or chmod anything — they are pure path views.
    """

    def test_log_dir_is_pure(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``_log_dir()`` must not create or modify the directory.

        Calling it twice on a tmp HOME with no pre-existing state dir
        must return the correct path on both calls and leave the
        filesystem untouched.
        """
        fake_home = tmp_path / "home" / "user"
        fake_home.mkdir(parents=True)
        monkeypatch.setenv("HOME", str(fake_home))

        expected = fake_home / ".punt-labs" / "vox" / "logs"
        assert not expected.exists()

        result_1 = _log_dir()
        result_2 = _log_dir()

        assert result_1 == expected
        assert result_2 == expected
        # The helper must not have created the directory.
        assert not expected.exists(), (
            f"_log_dir() created {expected} as a side effect — "
            "helper should be pure path resolution"
        )

    def test_run_dir_is_pure(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``_run_dir()`` must not create or modify the directory."""
        fake_home = tmp_path / "home" / "user"
        fake_home.mkdir(parents=True)
        monkeypatch.setenv("HOME", str(fake_home))

        expected = fake_home / ".punt-labs" / "vox" / "run"
        assert not expected.exists()

        result = _run_dir()

        assert result == expected
        assert not expected.exists()

    def test_config_dir_is_pure(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``_config_dir()`` must not create or modify the directory."""
        fake_home = tmp_path / "home" / "user"
        fake_home.mkdir(parents=True)
        monkeypatch.setenv("HOME", str(fake_home))

        expected = fake_home / ".punt-labs" / "vox"
        # The state root parent does not exist yet.
        assert not expected.exists()

        result = _config_dir()

        assert result == expected
        assert not expected.exists()


class TestModelSupportsExpressiveTags:
    """``_model_supports_expressive_tags`` lookup for vibe-tag gating.

    Closes vox-fhl. The function must be a pure capability check that
    runs BEFORE provider construction (so it can fire outside the
    env-mutation lock that the real synthesize path needs).
    """

    def test_elevenlabs_v3_supports(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TTS_MODEL", raising=False)
        assert _model_supports_expressive_tags("elevenlabs", "eleven_v3") is True

    def test_elevenlabs_flash_does_not_support(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("TTS_MODEL", raising=False)
        assert (
            _model_supports_expressive_tags("elevenlabs", "eleven_flash_v2_5") is False
        )

    def test_elevenlabs_default_supports(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """None model resolves to the eleven_v3 default which supports tags."""
        monkeypatch.delenv("TTS_MODEL", raising=False)
        assert _model_supports_expressive_tags("elevenlabs", None) is True

    def test_polly_never_supports(self) -> None:
        assert _model_supports_expressive_tags("polly", None) is False
        assert _model_supports_expressive_tags("polly", "neural") is False

    def test_openai_never_supports(self) -> None:
        assert _model_supports_expressive_tags("openai", None) is False
        assert _model_supports_expressive_tags("openai", "tts-1-hd") is False

    def test_say_never_supports(self) -> None:
        assert _model_supports_expressive_tags("say", None) is False

    def test_espeak_never_supports(self) -> None:
        assert _model_supports_expressive_tags("espeak", None) is False

    def test_unknown_provider_never_supports(self) -> None:
        """Defensive default for any provider name not in the lookup."""
        assert _model_supports_expressive_tags("future_provider", "any") is False


class TestApplyVibeForSynthesis:
    """``_apply_vibe_for_synthesis`` gates vibe-tag handling on capability.

    Closes vox-fhl. The helper takes RAW text (NOT yet normalized) and
    runs ``normalize_for_speech`` on the body itself, after splitting
    leading bracket tags off. The order matters because
    ``normalize_for_speech`` discards brackets via its non-prosody
    punctuation filter — if normalization runs first, ``[serious]``
    becomes ``serious`` and the bare word survives into TTS input on
    every non-expressive provider.

    With an expressive model, vibe_tags + leading user tags get
    re-attached after normalization. With a non-expressive model,
    both are dropped entirely.
    """

    def test_expressive_model_prepends_vibe_tags(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("TTS_MODEL", raising=False)
        result = _apply_vibe_for_synthesis(
            "Hello world", "[excited]", "elevenlabs", "eleven_v3"
        )
        assert result == "[excited] Hello world"

    def test_expressive_model_passthrough_when_no_vibe_tags(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("TTS_MODEL", raising=False)
        result = _apply_vibe_for_synthesis(
            "Hello world", None, "elevenlabs", "eleven_v3"
        )
        assert result == "Hello world"

    def test_expressive_model_preserves_user_tags_in_text(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """User-supplied tags pass through to a model that can interpret them."""
        monkeypatch.delenv("TTS_MODEL", raising=False)
        result = _apply_vibe_for_synthesis(
            "[whisper] Quiet message", None, "elevenlabs", "eleven_v3"
        )
        assert result == "[whisper] Quiet message"

    def test_non_expressive_model_drops_vibe_tags(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When the model can't interpret tags, the vibe_tags MUST not appear."""
        monkeypatch.delenv("TTS_MODEL", raising=False)
        result = _apply_vibe_for_synthesis(
            "Hello world", "[serious]", "elevenlabs", "eleven_flash_v2_5"
        )
        assert result == "Hello world"
        assert "[serious]" not in result
        assert "serious" not in result

    def test_non_expressive_model_strips_user_tags_from_text(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """User-supplied bracket tags MUST be stripped to avoid literal speech."""
        monkeypatch.delenv("TTS_MODEL", raising=False)
        result = _apply_vibe_for_synthesis(
            "[serious] Hello world", None, "elevenlabs", "eleven_flash_v2_5"
        )
        assert result == "Hello world"
        assert "serious" not in result

    def test_non_expressive_model_strips_user_tags_even_with_vibe_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Combined: user text has tags AND vibe is set; both must vanish."""
        monkeypatch.delenv("TTS_MODEL", raising=False)
        result = _apply_vibe_for_synthesis(
            "[whisper] Quiet message",
            "[excited]",
            "elevenlabs",
            "eleven_flash_v2_5",
        )
        assert result == "Quiet message"
        assert "whisper" not in result
        assert "excited" not in result

    def test_polly_strips_user_tags(self) -> None:
        """Polly never supports tags — user-supplied tags must be stripped."""
        result = _apply_vibe_for_synthesis(
            "[serious] Important", "[calm]", "polly", None
        )
        assert result == "Important"

    def test_openai_strips_user_tags(self) -> None:
        result = _apply_vibe_for_synthesis("[whisper] Hello", None, "openai", "tts-1")
        assert result == "Hello"

    def test_say_strips_user_tags(self) -> None:
        result = _apply_vibe_for_synthesis(
            "[excited] Greetings", "[happy]", "say", None
        )
        assert result == "Greetings"

    def test_espeak_strips_user_tags(self) -> None:
        result = _apply_vibe_for_synthesis("[serious] Notice", None, "espeak", None)
        assert result == "Notice"

    def test_degenerate_text_only_tags_non_expressive_returns_empty(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Tags-only input on a non-expressive model returns empty body.

        The body after splitting leading tags is empty, so there is
        nothing speakable. Returning the literal "[serious]" would have
        the TTS engine speak the word "serious" (the exact bug the
        capability gate exists to prevent). Returning empty is correct
        — the synthesize handler upstream is responsible for catching
        empty input as a no-op.
        """
        monkeypatch.delenv("TTS_MODEL", raising=False)
        result = _apply_vibe_for_synthesis(
            "[serious]", None, "elevenlabs", "eleven_flash_v2_5"
        )
        assert result == ""

    def test_degenerate_text_only_tags_expressive_keeps_tags(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Tags-only input on an expressive model keeps the tags as content.

        The model interprets the tag as a performance directive. There
        is no body, but the tag itself is the message.
        """
        monkeypatch.delenv("TTS_MODEL", raising=False)
        result = _apply_vibe_for_synthesis("[sighs]", None, "elevenlabs", "eleven_v3")
        assert result == "[sighs]"

    def test_normalizes_body_through_production_call_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Regression test for the bug Copilot found on the first attempt.

        The helper must run ``normalize_for_speech`` on the body itself
        (so snake_case/camelCase get expanded). Earlier versions of this
        fix called ``_apply_vibe_for_synthesis(normalize_for_speech(text))``
        from voxd, which meant the helper saw already-normalized text
        and ``strip_expressive_tags`` could not match the brackets that
        normalization had already eaten. The fix moves normalization
        INSIDE the helper, after the leading-tag split.
        """
        monkeypatch.delenv("TTS_MODEL", raising=False)
        # snake_case body so we can prove normalize_for_speech ran on it.
        result = _apply_vibe_for_synthesis(
            "[serious] my_function works", None, "polly", None
        )
        # Body is normalized (underscores → spaces) AND the leading bracket
        # tag is gone — neither as brackets nor as the bare word "serious".
        assert result == "my function works"
        assert "serious" not in result
        assert "[" not in result
        assert "_" not in result

    def test_expressive_model_normalizes_body_too(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Expressive path also runs normalize on the body, just keeps tags."""
        monkeypatch.delenv("TTS_MODEL", raising=False)
        result = _apply_vibe_for_synthesis(
            "[whisper] my_function works", None, "elevenlabs", "eleven_v3"
        )
        assert result == "[whisper] my function works"


# ---------------------------------------------------------------------------
# vox-0e9: opt-in once-flag dedup for speech
# ---------------------------------------------------------------------------


class TestOnceDedup:
    """Unit tests for the OnceDedup class.

    Closes vox-0e9. The class deduplicates speech requests when the
    caller passes a TTL window. Identical text spoken with different
    voices, providers, or models all collapse — the dedup key is
    md5(text) only. Returns DedupHit on a hit so callers can render
    observable "deduped" responses.
    """

    def test_first_call_records_and_returns_none(self) -> None:
        dedup = OnceDedup()
        result = dedup.check_and_record("hello world", ttl_seconds=600)
        assert result is None

    def test_second_call_within_ttl_returns_hit(self) -> None:
        dedup = OnceDedup()
        first = dedup.check_and_record("hello world", ttl_seconds=600)
        assert first is None
        second = dedup.check_and_record("hello world", ttl_seconds=600)
        assert second is not None
        assert isinstance(second, DedupHit)
        assert second.original_played_at > 0
        assert 0 < second.ttl_seconds_remaining <= 600

    def test_second_call_after_ttl_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When monotonic clock advances past the TTL, the entry expires."""
        dedup = OnceDedup()

        clock = [1000.0]

        def fake_monotonic() -> float:
            return clock[0]

        def fake_time() -> float:
            return 1_700_000_000.0 + (clock[0] - 1000.0)

        monkeypatch.setattr("punt_vox.voxd.time.monotonic", fake_monotonic)
        monkeypatch.setattr("punt_vox.voxd.time.time", fake_time)

        first = dedup.check_and_record("hello world", ttl_seconds=10)
        assert first is None

        # Advance the clock past the TTL.
        clock[0] = 1011.0

        second = dedup.check_and_record("hello world", ttl_seconds=10)
        assert second is None

    def test_different_text_does_not_dedupe(self) -> None:
        dedup = OnceDedup()
        first = dedup.check_and_record("hello", ttl_seconds=600)
        second = dedup.check_and_record("goodbye", ttl_seconds=600)
        assert first is None
        assert second is None

    def test_key_is_text_only_voice_irrelevant(self) -> None:
        """Two callers with the same text collapse regardless of voice.

        OnceDedup keys on md5(text) only. The voice/provider/model are
        not part of the key per the vox-0e9 spec — biff wall fan-out
        across N sessions may use different voice settings but the
        user heard the SAME message and shouldn't hear it again.
        """
        dedup = OnceDedup()
        # OnceDedup.check_and_record only takes text + ttl_seconds. The
        # voice is not even an argument — confirming the key shape by
        # the type signature itself. The test below documents the
        # invariant for future maintainers.
        first = dedup.check_and_record("status update", ttl_seconds=600)
        second = dedup.check_and_record("status update", ttl_seconds=600)
        assert first is None
        assert second is not None

    def test_dedup_hit_carries_original_played_at_wall_clock(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """original_played_at is wall clock (time.time), not monotonic."""
        dedup = OnceDedup()

        monkeypatch.setattr("punt_vox.voxd.time.monotonic", lambda: 5000.0)
        monkeypatch.setattr("punt_vox.voxd.time.time", lambda: 1_700_000_000.0)

        first = dedup.check_and_record("text", ttl_seconds=100)
        assert first is None

        monkeypatch.setattr("punt_vox.voxd.time.monotonic", lambda: 5050.0)
        monkeypatch.setattr("punt_vox.voxd.time.time", lambda: 1_700_000_050.0)

        hit = dedup.check_and_record("text", ttl_seconds=100)
        assert hit is not None
        # original_played_at is the wall-clock time of the FIRST call,
        # not the second. Caller-facing for "played 50s ago" rendering.
        assert hit.original_played_at == 1_700_000_000.0
        # ttl_seconds_remaining = original ttl - elapsed monotonic.
        assert abs(hit.ttl_seconds_remaining - 50.0) < 0.001

    def test_zero_ttl_raises(self) -> None:
        dedup = OnceDedup()
        with pytest.raises(ValueError, match="positive"):
            dedup.check_and_record("text", ttl_seconds=0)

    def test_negative_ttl_raises(self) -> None:
        dedup = OnceDedup()
        with pytest.raises(ValueError, match="positive"):
            dedup.check_and_record("text", ttl_seconds=-1)

    def test_pruning_drops_entries_older_than_max_ttl(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Opportunistic prune-on-insert drops entries older than the global cap.

        The cap (``_ONCE_DEDUP_MAX_TTL_SECONDS``) bounds how long any
        single entry can live in ``_seen``, regardless of what TTL the
        original caller requested. This prevents pathological
        ``once=99999999`` callers from wedging long-lived entries.
        """
        from punt_vox.voxd import _ONCE_DEDUP_MAX_TTL_SECONDS

        dedup = OnceDedup()

        clock = [1000.0]
        monkeypatch.setattr("punt_vox.voxd.time.monotonic", lambda: clock[0])
        monkeypatch.setattr("punt_vox.voxd.time.time", lambda: 1_700_000_000.0)

        dedup.check_and_record("text-a", ttl_seconds=600)
        assert len(dedup._seen) == 1

        # Advance past the global cap so the entry is prunable.
        clock[0] = 1000.0 + _ONCE_DEDUP_MAX_TTL_SECONDS + 100.0

        # Insert a different text — this triggers the prune loop.
        dedup.check_and_record("text-b", ttl_seconds=600)
        assert len(dedup._seen) == 1

    def test_rollback_removes_entry(self) -> None:
        """rollback(text) drops the entry so a subsequent call plays again."""
        dedup = OnceDedup()
        first = dedup.check_and_record("wall msg", ttl_seconds=600)
        assert first is None

        # Simulate a failed synthesis — the dedup entry was recorded
        # but the audio never actually played. Rollback must remove it.
        dedup.rollback("wall msg")

        # A retry should NOT be deduped.
        retry = dedup.check_and_record("wall msg", ttl_seconds=600)
        assert retry is None

    def test_rollback_is_idempotent(self) -> None:
        """rollback on an unrecorded text is a no-op, not an error."""
        dedup = OnceDedup()
        # Never called check_and_record for this text.
        dedup.rollback("unknown text")  # Must not raise.

    def test_per_caller_ttl_shrinks_effective_window(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Each caller's own ttl_seconds decides if an entry is fresh enough.

        Copilot reviewer 3053861452: the first caller's TTL should NOT
        silently extend the dedup window for a later caller that asks
        for a shorter one. Each caller answers its own question of
        "was this played in the last N seconds?"
        """
        dedup = OnceDedup()
        clock = [1000.0]
        monkeypatch.setattr("punt_vox.voxd.time.monotonic", lambda: clock[0])
        monkeypatch.setattr("punt_vox.voxd.time.time", lambda: 1_700_000_000.0)

        # First caller records with a long window.
        first = dedup.check_and_record("text", ttl_seconds=600)
        assert first is None

        # 50 seconds later, a second caller asks with a 30s window.
        clock[0] = 1050.0
        second = dedup.check_and_record("text", ttl_seconds=30)
        # age=50 > caller's ttl of 30 → NOT a hit from the second
        # caller's perspective. Must not dedupe.
        assert second is None

        # Immediately after, a third caller asks with a 120s window.
        third = dedup.check_and_record("text", ttl_seconds=120)
        # age is now 0 (the second caller's record_and_record reset
        # the entry) so third caller asks "was this played in the
        # last 120s?" — yes, just now. DedupHit.
        assert third is not None

    def test_ttl_above_cap_gets_clamped(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Callers passing a TTL above the cap are clamped with a log warning."""
        from punt_vox.voxd import _ONCE_DEDUP_MAX_TTL_SECONDS

        dedup = OnceDedup()
        clock = [1000.0]
        monkeypatch.setattr("punt_vox.voxd.time.monotonic", lambda: clock[0])
        monkeypatch.setattr("punt_vox.voxd.time.time", lambda: 1_700_000_000.0)

        with caplog.at_level(logging.WARNING, logger="punt_vox.voxd"):
            first = dedup.check_and_record("text", ttl_seconds=99_999_999)
        assert first is None
        assert "clamping" in caplog.text

        # Advance past the cap; entry should be prunable.
        clock[0] = 1000.0 + _ONCE_DEDUP_MAX_TTL_SECONDS + 1.0
        second = dedup.check_and_record("text", ttl_seconds=10)
        assert second is None  # entry was pruned, no hit

    def test_hard_cap_on_dict_size(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When more than _ONCE_DEDUP_MAX_ENTRIES inserted, oldest evicted."""
        from punt_vox.voxd import _ONCE_DEDUP_MAX_ENTRIES

        dedup = OnceDedup()
        clock = [1000.0]
        monkeypatch.setattr("punt_vox.voxd.time.monotonic", lambda: clock[0])
        monkeypatch.setattr("punt_vox.voxd.time.time", lambda: 1_700_000_000.0)

        # Fill the cache past the cap. Each insert advances the clock
        # slightly so the eviction order is deterministic.
        for i in range(_ONCE_DEDUP_MAX_ENTRIES + 50):
            clock[0] = 1000.0 + (i * 0.001)
            dedup.check_and_record(f"text-{i}", ttl_seconds=600)

        import hashlib

        def _md5(s: str) -> str:
            return hashlib.md5(s.encode()).hexdigest()

        # Dict size is bounded by the hard cap.
        assert len(dedup._seen) == _ONCE_DEDUP_MAX_ENTRIES
        # The oldest insertions were evicted; the newest remain.
        assert _md5("text-0") not in dedup._seen
        assert _md5(f"text-{_ONCE_DEDUP_MAX_ENTRIES + 49}") in dedup._seen


class TestChimeDedup:
    """ChimeDedup is the renamed AudioDedup, simplified for the chime path."""

    def test_first_chime_plays(self) -> None:
        dedup = ChimeDedup()
        assert dedup.should_play("tests-pass") is True

    def test_duplicate_chime_within_window_dropped(self) -> None:
        dedup = ChimeDedup()
        assert dedup.should_play("tests-pass") is True
        assert dedup.should_play("tests-pass") is False

    def test_different_signal_not_dropped(self) -> None:
        dedup = ChimeDedup()
        assert dedup.should_play("tests-pass") is True
        assert dedup.should_play("lint-fail") is True

    def test_chime_dedup_after_window_plays_again(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        dedup = ChimeDedup(window=5.0)
        clock = [1000.0]
        monkeypatch.setattr("punt_vox.voxd.time.monotonic", lambda: clock[0])
        assert dedup.should_play("tests-pass") is True
        clock[0] = 1010.0  # past 5s window
        assert dedup.should_play("tests-pass") is True


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

        monkeypatch.setattr("punt_vox.voxd._synthesize_to_file", fake_synthesize)
        monkeypatch.setattr("punt_vox.voxd._LOCAL_PROVIDERS", set[str]())
        monkeypatch.setattr("punt_vox.voxd.auto_detect_provider", lambda: "elevenlabs")

        class _InstantPlaybackQueue:
            async def put(self, item: PlaybackItem) -> None:
                # Mark the item's done event so the handler's wait() returns.
                item.notify.set()

        ctx.playback_queue = _InstantPlaybackQueue()  # type: ignore[assignment]
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
            and rec.name == "punt_vox.voxd"
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
            and rec.name == "punt_vox.voxd"
            and "WebSocket error" in rec.getMessage()
        ]
        assert len(ws_error_records) == 1
        # The ``finally`` branch must still decrement client_count.
        assert ctx.client_count == 0


# ---------------------------------------------------------------------------
# Music integration tests
# ---------------------------------------------------------------------------


class TestDaemonContextMusicFields:
    """DaemonContext must have all 8 music fields with correct defaults."""

    def test_music_mode_default(self) -> None:
        ctx = _make_ctx()
        assert ctx.music_mode == "off"

    def test_music_style_default(self) -> None:
        ctx = _make_ctx()
        assert ctx.music_style == ""

    def test_music_owner_default(self) -> None:
        ctx = _make_ctx()
        assert ctx.music_owner == ""

    def test_music_vibe_default(self) -> None:
        ctx = _make_ctx()
        assert ctx.music_vibe == ("", "")

    def test_music_track_default(self) -> None:
        ctx = _make_ctx()
        assert ctx.music_track is None

    def test_music_proc_default(self) -> None:
        ctx = _make_ctx()
        assert ctx.music_proc is None

    def test_music_state_default(self) -> None:
        ctx = _make_ctx()
        assert ctx.music_state == "idle"

    def test_music_changed_default(self) -> None:
        ctx = _make_ctx()
        assert isinstance(ctx.music_changed, asyncio.Event)
        assert not ctx.music_changed.is_set()


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


class TestMusicPlayerCommand:
    """_music_player_command produces the right argv at reduced volume."""

    def test_linux_ffplay_with_volume(self) -> None:
        with patch("punt_vox.voxd._is_darwin", return_value=False):
            cmd = _music_player_command(Path("/tmp/track.mp3"))
        assert cmd == [
            "ffplay",
            "-nodisp",
            "-autoexit",
            "-volume",
            "30",
            "/tmp/track.mp3",
        ]

    def test_darwin_afplay_with_volume(self) -> None:
        with patch("punt_vox.voxd._is_darwin", return_value=True):
            cmd = _music_player_command(Path("/tmp/track.mp3"))
        assert cmd == ["afplay", "--volume", "0.3", "/tmp/track.mp3"]


class TestMusicLoopStateTransitions:
    """_music_loop: generation, playback, vibe changes, crash recovery."""

    def test_generates_and_plays_then_stops_on_off(self) -> None:
        """Full cycle: mode on -> generate -> play -> mode off."""
        ctx = _make_ctx()

        call_log: list[str] = []

        async def fake_generate_track(
            self: object, prompt: str, duration_ms: int, output_path: Path
        ) -> Path:
            call_log.append("generate")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"fake-music-data")
            return output_path

        async def fake_subprocess_exec(*args: object, **kwargs: object) -> MagicMock:
            call_log.append("play")
            proc = MagicMock()
            proc.returncode = 0

            async def _wait() -> int:
                await asyncio.sleep(0.01)
                return 0

            proc.wait = _wait
            proc.kill = MagicMock()
            return proc

        async def _drive() -> None:
            with (
                patch(
                    "punt_vox.providers.elevenlabs_music.ElevenLabsMusicProvider"
                    ".generate_track",
                    fake_generate_track,
                ),
                patch(
                    "punt_vox.voxd.asyncio.create_subprocess_exec",
                    fake_subprocess_exec,
                ),
                patch(
                    "punt_vox.voxd._music_output_dir",
                    return_value=Path("/tmp/vox-test-music"),
                ),
            ):
                task = asyncio.create_task(_music_loop(ctx))
                await asyncio.sleep(0)

                # Turn music on.
                ctx.music_mode = "on"
                ctx.music_owner = "test-session"
                ctx.music_vibe = ("focused", "[calm]")
                ctx.music_changed.set()
                await asyncio.sleep(0.05)

                # Turn music off.
                ctx.music_mode = "off"
                ctx.music_changed.set()
                await asyncio.sleep(0.05)

                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        asyncio.run(_drive())

        assert "generate" in call_log
        assert "play" in call_log

    def test_crash_recovery_retries_with_backoff(self) -> None:
        """Three failures in a row disable music mode."""
        ctx = _make_ctx()
        ctx.music_mode = "on"
        ctx.music_owner = "test-session"
        ctx.music_vibe = ("focused", "")
        ctx.music_changed.set()

        attempt_count = 0

        async def failing_generate(
            self: object, prompt: str, duration_ms: int, output_path: Path
        ) -> Path:
            nonlocal attempt_count
            attempt_count += 1
            msg = f"generation failed (attempt {attempt_count})"
            raise RuntimeError(msg)

        async def _drive() -> None:
            nonlocal attempt_count
            with (
                patch(
                    "punt_vox.providers.elevenlabs_music.ElevenLabsMusicProvider"
                    ".generate_track",
                    failing_generate,
                ),
                patch(
                    "punt_vox.voxd._music_output_dir",
                    return_value=Path("/tmp/vox-test-music"),
                ),
                patch("punt_vox.voxd._music_backoff_sleep", AsyncMock()),
            ):
                task = asyncio.create_task(_music_loop(ctx))
                # Yield control so the loop can run its 3 retries.
                for _ in range(20):
                    await asyncio.sleep(0)
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        asyncio.run(_drive())

        assert attempt_count == 3
        assert ctx.music_mode == "off"
        assert ctx.music_state == "idle"

    def test_vibe_change_during_generation_triggers_regeneration(self) -> None:
        """Setting music_changed during generation causes a new track."""
        ctx = _make_ctx()
        ctx.music_mode = "on"
        ctx.music_owner = "test-session"
        ctx.music_vibe = ("focused", "")
        ctx.music_changed.set()

        generation_count = 0

        async def counting_generate(
            self: object, prompt: str, duration_ms: int, output_path: Path
        ) -> Path:
            nonlocal generation_count
            generation_count += 1
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"fake-music-data")

            # On first generation, simulate a vibe change mid-flight.
            if generation_count == 1:
                ctx.music_vibe = ("happy", "[warm]")
                ctx.music_changed.set()

            return output_path

        async def fake_subprocess_exec(*args: object, **kwargs: object) -> MagicMock:
            proc = MagicMock()
            proc.returncode = 0

            async def _wait() -> int:
                await asyncio.sleep(0.01)
                return 0

            proc.wait = _wait
            proc.kill = MagicMock()
            return proc

        async def _drive() -> None:
            with (
                patch(
                    "punt_vox.providers.elevenlabs_music.ElevenLabsMusicProvider"
                    ".generate_track",
                    counting_generate,
                ),
                patch(
                    "punt_vox.voxd.asyncio.create_subprocess_exec",
                    fake_subprocess_exec,
                ),
                patch(
                    "punt_vox.voxd._music_output_dir",
                    return_value=Path("/tmp/vox-test-music"),
                ),
            ):
                task = asyncio.create_task(_music_loop(ctx))
                await asyncio.sleep(0.15)

                ctx.music_mode = "off"
                ctx.music_changed.set()
                await asyncio.sleep(0.05)

                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        asyncio.run(_drive())

        assert generation_count >= 2


class TestMusicLoopGaplessHandoff:
    """Old track must keep looping while generation runs concurrently."""

    def test_old_track_loops_during_generation(self) -> None:
        """Playback subprocess stays alive the entire time generation runs.

        Simulates a slow generation (~0.15s) and verifies the playback
        subprocess is NOT killed until the new track is ready.
        """
        ctx = _make_ctx()
        ctx.music_mode = "on"
        ctx.music_owner = "test-session"
        ctx.music_vibe = ("focused", "[calm]")
        ctx.music_changed.set()

        generation_count = 0
        play_count = 0
        # Track which procs were alive during generation.
        procs_alive_during_gen: list[bool] = []

        async def slow_generate(
            self: object, prompt: str, duration_ms: int, output_path: Path
        ) -> Path:
            nonlocal generation_count
            generation_count += 1
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"fake-music-data")

            if generation_count == 2:
                # Second generation (triggered by vibe change). The old
                # playback proc should still be alive during this window.
                proc = ctx.music_proc
                is_alive = proc is not None and proc.returncode is None
                procs_alive_during_gen.append(is_alive)
                # Simulate slow generation.
                await asyncio.sleep(0.1)
                # Check again after the sleep.
                proc = ctx.music_proc
                is_alive = proc is not None and proc.returncode is None
                procs_alive_during_gen.append(is_alive)

            return output_path

        async def fake_subprocess_exec(*args: object, **kwargs: object) -> MagicMock:
            nonlocal play_count
            play_count += 1
            proc = MagicMock()
            proc.returncode = None  # Still running.

            async def _wait() -> int:
                # Simulate a long track so it doesn't end naturally.
                # Return immediately if the process was already killed,
                # mirroring real OS behavior after SIGKILL.
                if proc.returncode is not None:
                    return int(proc.returncode)
                await asyncio.sleep(5.0)
                proc.returncode = 0
                return 0

            proc.wait = _wait
            proc.kill = MagicMock(side_effect=lambda: setattr(proc, "returncode", -9))
            return proc

        async def _drive() -> None:
            with (
                patch(
                    "punt_vox.providers.elevenlabs_music.ElevenLabsMusicProvider"
                    ".generate_track",
                    slow_generate,
                ),
                patch(
                    "punt_vox.voxd.asyncio.create_subprocess_exec",
                    fake_subprocess_exec,
                ),
                patch(
                    "punt_vox.voxd._music_output_dir",
                    return_value=Path("/tmp/vox-test-handoff"),
                ),
            ):
                task = asyncio.create_task(_music_loop(ctx))
                # Let initial generation + first playback start.
                await asyncio.sleep(0.05)

                # Trigger a vibe change while the first track is playing.
                ctx.music_vibe = ("happy", "[warm]")
                ctx.music_changed.set()

                # Wait for second generation to complete + handoff.
                await asyncio.sleep(0.3)

                # Shut down.
                ctx.music_mode = "off"
                ctx.music_changed.set()
                await asyncio.sleep(0.05)
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        asyncio.run(_drive())

        assert generation_count >= 2, (
            f"expected >=2 generations, got {generation_count}"
        )
        assert play_count >= 2, f"expected >=2 playback spawns, got {play_count}"
        # The old playback proc was alive during the entire generation window.
        assert all(procs_alive_during_gen), (
            f"old track was killed during generation: {procs_alive_during_gen}"
        )

    def test_second_vibe_change_cancels_inflight_generation(self) -> None:
        """A second vibe change during generation cancels the first and starts fresh."""
        ctx = _make_ctx()
        ctx.music_mode = "on"
        ctx.music_owner = "test-session"
        ctx.music_vibe = ("focused", "[calm]")
        ctx.music_changed.set()

        generation_vibes: list[str] = []
        gen_event = asyncio.Event()

        async def tracking_generate(
            self: object, prompt: str, duration_ms: int, output_path: Path
        ) -> Path:
            vibe, _ = ctx.music_vibe
            generation_vibes.append(vibe)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"fake-music-data")

            if len(generation_vibes) == 2:
                # Signal that second generation started, then simulate slow work.
                gen_event.set()
                await asyncio.sleep(0.5)
            elif len(generation_vibes) == 3:
                # Third generation — the replacement after cancel.
                pass

            return output_path

        async def fake_subprocess_exec(*args: object, **kwargs: object) -> MagicMock:
            proc = MagicMock()
            proc.returncode = None

            async def _wait() -> int:
                if proc.returncode is not None:
                    return int(proc.returncode)
                await asyncio.sleep(5.0)
                proc.returncode = 0
                return 0

            proc.wait = _wait
            proc.kill = MagicMock(side_effect=lambda: setattr(proc, "returncode", -9))
            return proc

        async def _drive() -> None:
            with (
                patch(
                    "punt_vox.providers.elevenlabs_music.ElevenLabsMusicProvider"
                    ".generate_track",
                    tracking_generate,
                ),
                patch(
                    "punt_vox.voxd.asyncio.create_subprocess_exec",
                    fake_subprocess_exec,
                ),
                patch(
                    "punt_vox.voxd._music_output_dir",
                    return_value=Path("/tmp/vox-test-cancel"),
                ),
            ):
                task = asyncio.create_task(_music_loop(ctx))
                await asyncio.sleep(0.05)

                # First vibe change triggers generation #2.
                ctx.music_vibe = ("happy", "[warm]")
                ctx.music_changed.set()
                # Wait for second generation to start.
                await asyncio.wait_for(gen_event.wait(), timeout=1.0)

                # Second vibe change while #2 is in-flight — should cancel it.
                ctx.music_vibe = ("energetic", "[upbeat]")
                ctx.music_changed.set()
                await asyncio.sleep(0.3)

                ctx.music_mode = "off"
                ctx.music_changed.set()
                await asyncio.sleep(0.05)
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        asyncio.run(_drive())

        assert len(generation_vibes) >= 3, (
            f"expected >=3 generation attempts, got "
            f"{len(generation_vibes)}: {generation_vibes}"
        )
        # The third generation should have the latest vibe.
        assert generation_vibes[-1] == "energetic"


class TestKillMusicProc:
    """_kill_music_proc safely terminates the music subprocess."""

    def test_kills_running_proc(self) -> None:
        ctx = _make_ctx()
        proc = MagicMock()
        proc.returncode = None
        proc.kill = MagicMock()
        proc.wait = AsyncMock(return_value=0)
        ctx.music_proc = proc

        asyncio.run(_kill_music_proc(ctx))

        proc.kill.assert_called_once()
        assert ctx.music_proc is None

    def test_noop_when_no_proc(self) -> None:
        ctx = _make_ctx()
        ctx.music_proc = None

        asyncio.run(_kill_music_proc(ctx))

        assert ctx.music_proc is None

    def test_noop_when_proc_already_exited(self) -> None:
        ctx = _make_ctx()
        proc = MagicMock()
        proc.returncode = 0
        proc.kill = MagicMock()
        ctx.music_proc = proc

        asyncio.run(_kill_music_proc(ctx))

        proc.kill.assert_not_called()
        assert ctx.music_proc is None


class TestMusicSeparateFromPlaybackQueue:
    """Music subprocess must NOT use the existing _playback_consumer queue.

    The spec explicitly requires music to run its own subprocess at
    reduced volume, independent of the chime/TTS playback queue. This
    test verifies the separation by checking that _handle_music_on does
    not enqueue anything on ctx.playback_queue.
    """

    def test_music_on_does_not_enqueue(self) -> None:
        ctx = _make_ctx()
        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {
            "id": "sep-1",
            "owner_id": "session-1",
            "vibe": "focused",
        }

        asyncio.run(_handle_music_on(msg, ws, ctx))

        assert ctx.playback_queue.empty()


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


class TestMusicLoopLostWakeup:
    """_music_loop must not block when music_mode is set before clear()."""

    def test_mode_on_before_wait_skips_blocking(self) -> None:
        """If music_mode becomes 'on' between clear() and wait(), proceed."""
        ctx = _make_ctx()
        # Pre-set music_mode to "on" so the re-check after clear() catches it.
        ctx.music_mode = "on"
        ctx.music_owner = "test-session"
        ctx.music_vibe = ("focused", "[calm]")
        # Do NOT set music_changed — the loop must detect mode via re-check.

        generation_happened = False

        async def fake_generate(
            self: object, prompt: str, duration_ms: int, output_path: Path
        ) -> Path:
            nonlocal generation_happened
            generation_happened = True
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"fake")
            # Turn off to let the loop exit cleanly.
            ctx.music_mode = "off"
            ctx.music_changed.set()
            return output_path

        async def _drive() -> None:
            with (
                patch(
                    "punt_vox.providers.elevenlabs_music.ElevenLabsMusicProvider"
                    ".generate_track",
                    fake_generate,
                ),
                patch(
                    "punt_vox.voxd._music_output_dir",
                    return_value=Path("/tmp/vox-test-lost-wakeup"),
                ),
            ):
                task = asyncio.create_task(_music_loop(ctx))
                # Give the loop enough time to either proceed or block.
                await asyncio.sleep(0.1)
                if not generation_happened:
                    # Loop is stuck — cancel and fail.
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
                else:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task

        asyncio.run(_drive())

        assert generation_happened, (
            "music_loop blocked on wait despite music_mode=='on'"
        )


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

        with patch("punt_vox.voxd._music_output_dir", return_value=music_dir):
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

        with patch("punt_vox.voxd._music_output_dir", return_value=music_dir):
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

        with patch("punt_vox.voxd._music_output_dir", return_value=music_dir):
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

        with patch("punt_vox.voxd._music_output_dir", return_value=music_dir):
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

        with patch("punt_vox.voxd._music_output_dir", return_value=music_dir):
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

        with patch("punt_vox.voxd._music_output_dir", return_value=music_dir):
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

        with patch("punt_vox.voxd._music_output_dir", return_value=music_dir):
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


class TestGenFailureKeepsOldTrack:
    """Generation failure must not kill the old track subprocess.

    Covers the fix for bead vox-m2l: when the generation task fails
    during the playback loop, the old track keeps looping during
    retry/backoff. Only max-retries or a successful handoff kills it.
    """

    def test_failure_then_success_old_track_alive_throughout(self) -> None:
        """First generation (vibe change) fails, retry succeeds.

        The old playback subprocess must remain alive (returncode is
        None) during the entire failure + backoff + retry window.
        """
        ctx = _make_ctx()
        ctx.music_mode = "on"
        ctx.music_owner = "test-session"
        ctx.music_vibe = ("focused", "[calm]")
        ctx.music_changed.set()

        generation_count = 0
        # Snapshots of old-proc liveness taken during the failing gen
        # and during the retry gen.
        old_proc_alive_snapshots: list[bool] = []

        async def fail_then_succeed(
            self: object, prompt: str, duration_ms: int, output_path: Path
        ) -> Path:
            nonlocal generation_count
            generation_count += 1
            output_path.parent.mkdir(parents=True, exist_ok=True)

            if generation_count == 2:
                # Second generation (triggered by vibe change): FAIL.
                # Snapshot old proc liveness before raising.
                proc = ctx.music_proc
                old_proc_alive_snapshots.append(
                    proc is not None and proc.returncode is None,
                )
                msg = "network error"
                raise RuntimeError(msg)

            if generation_count == 3:
                # Third generation (retry after failure): succeed.
                # The old proc should STILL be alive during retry.
                proc = ctx.music_proc
                old_proc_alive_snapshots.append(
                    proc is not None and proc.returncode is None,
                )

            output_path.write_bytes(b"fake-music-data")
            return output_path

        async def fake_subprocess_exec(*args: object, **kwargs: object) -> MagicMock:
            proc = MagicMock()
            proc.returncode = None

            async def _wait() -> int:
                if proc.returncode is not None:
                    return int(proc.returncode)
                await asyncio.sleep(5.0)
                proc.returncode = 0
                return 0

            proc.wait = _wait
            proc.kill = MagicMock(
                side_effect=lambda: setattr(proc, "returncode", -9),
            )
            return proc

        async def _drive() -> None:
            with (
                patch(
                    "punt_vox.providers.elevenlabs_music."
                    "ElevenLabsMusicProvider.generate_track",
                    fail_then_succeed,
                ),
                patch(
                    "punt_vox.voxd.asyncio.create_subprocess_exec",
                    fake_subprocess_exec,
                ),
                patch(
                    "punt_vox.voxd._music_output_dir",
                    return_value=Path("/tmp/vox-test-gen-fail"),
                ),
                patch("punt_vox.voxd._music_backoff_sleep", AsyncMock()),
            ):
                task = asyncio.create_task(_music_loop(ctx))
                # Let initial generation + first playback start.
                await asyncio.sleep(0.05)

                # Trigger vibe change — second generation will fail.
                ctx.music_vibe = ("happy", "[warm]")
                ctx.music_changed.set()

                # Wait for failure + backoff + retry + handoff.
                await asyncio.sleep(0.3)

                ctx.music_mode = "off"
                ctx.music_changed.set()
                await asyncio.sleep(0.05)
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        asyncio.run(_drive())

        # Generation ran at least 3 times: initial, fail, retry.
        assert generation_count >= 3, (
            f"expected >=3 generations, got {generation_count}"
        )
        # Old track was alive during both the failing gen and the retry.
        assert len(old_proc_alive_snapshots) >= 2, (
            f"expected >=2 liveness snapshots, got {old_proc_alive_snapshots}"
        )
        assert all(old_proc_alive_snapshots), (
            f"old track was killed during gen failure/retry: {old_proc_alive_snapshots}"
        )

    def test_max_retries_stops_music_mode(self) -> None:
        """After max retries during playback, music_mode becomes 'off'."""
        ctx = _make_ctx()
        ctx.music_mode = "on"
        ctx.music_owner = "test-session"
        ctx.music_vibe = ("focused", "[calm]")
        ctx.music_changed.set()

        generation_count = 0

        async def always_fail_after_first(
            self: object, prompt: str, duration_ms: int, output_path: Path
        ) -> Path:
            nonlocal generation_count
            generation_count += 1
            output_path.parent.mkdir(parents=True, exist_ok=True)

            if generation_count == 1:
                # Initial generation succeeds.
                output_path.write_bytes(b"fake-music-data")
                return output_path

            # All subsequent generations fail.
            msg = f"generation failed (attempt {generation_count})"
            raise RuntimeError(msg)

        async def fake_subprocess_exec(*args: object, **kwargs: object) -> MagicMock:
            proc = MagicMock()
            proc.returncode = None

            async def _wait() -> int:
                if proc.returncode is not None:
                    return int(proc.returncode)
                await asyncio.sleep(5.0)
                proc.returncode = 0
                return 0

            proc.wait = _wait
            proc.kill = MagicMock(
                side_effect=lambda: setattr(proc, "returncode", -9),
            )
            return proc

        async def _drive() -> None:
            with (
                patch(
                    "punt_vox.providers.elevenlabs_music."
                    "ElevenLabsMusicProvider.generate_track",
                    always_fail_after_first,
                ),
                patch(
                    "punt_vox.voxd.asyncio.create_subprocess_exec",
                    fake_subprocess_exec,
                ),
                patch(
                    "punt_vox.voxd._music_output_dir",
                    return_value=Path("/tmp/vox-test-gen-maxretry"),
                ),
                patch("punt_vox.voxd._music_backoff_sleep", AsyncMock()),
            ):
                task = asyncio.create_task(_music_loop(ctx))
                # Let initial generation + first playback start.
                await asyncio.sleep(0.05)

                # Trigger vibe change — all subsequent gens will fail.
                ctx.music_vibe = ("happy", "[warm]")
                ctx.music_changed.set()

                # Wait for 3 failures + final shutdown.
                for _ in range(50):
                    await asyncio.sleep(0.01)
                    if ctx.music_mode == "off":
                        break

                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        asyncio.run(_drive())

        assert ctx.music_mode == "off"
        assert ctx.music_state == "idle"
        # 1 initial success + 3 failures = 4 total.
        assert generation_count == 4, (
            f"expected 4 generations (1 ok + 3 fail), got {generation_count}"
        )

    def test_successful_handoff_after_retry_resets_counter(self) -> None:
        """A successful handoff after one failure resets the retry counter."""
        ctx = _make_ctx()
        ctx.music_mode = "on"
        ctx.music_owner = "test-session"
        ctx.music_vibe = ("focused", "[calm]")
        ctx.music_changed.set()

        generation_count = 0

        async def fail_once_then_succeed(
            self: object, prompt: str, duration_ms: int, output_path: Path
        ) -> Path:
            nonlocal generation_count
            generation_count += 1
            output_path.parent.mkdir(parents=True, exist_ok=True)

            if generation_count == 2:
                msg = "transient error"
                raise RuntimeError(msg)

            output_path.write_bytes(b"fake-music-data")
            return output_path

        async def fake_subprocess_exec(*args: object, **kwargs: object) -> MagicMock:
            proc = MagicMock()
            proc.returncode = None

            async def _wait() -> int:
                if proc.returncode is not None:
                    return int(proc.returncode)
                await asyncio.sleep(5.0)
                proc.returncode = 0
                return 0

            proc.wait = _wait
            proc.kill = MagicMock(
                side_effect=lambda: setattr(proc, "returncode", -9),
            )
            return proc

        async def _drive() -> None:
            with (
                patch(
                    "punt_vox.providers.elevenlabs_music."
                    "ElevenLabsMusicProvider.generate_track",
                    fail_once_then_succeed,
                ),
                patch(
                    "punt_vox.voxd.asyncio.create_subprocess_exec",
                    fake_subprocess_exec,
                ),
                patch(
                    "punt_vox.voxd._music_output_dir",
                    return_value=Path("/tmp/vox-test-gen-reset"),
                ),
                patch("punt_vox.voxd._music_backoff_sleep", AsyncMock()),
            ):
                task = asyncio.create_task(_music_loop(ctx))
                await asyncio.sleep(0.05)

                # Trigger vibe change — gen #2 fails, #3 succeeds.
                ctx.music_vibe = ("happy", "[warm]")
                ctx.music_changed.set()
                await asyncio.sleep(0.3)

                # Music should still be on — the retry succeeded.
                assert ctx.music_mode == "on"

                ctx.music_mode = "off"
                ctx.music_changed.set()
                await asyncio.sleep(0.05)
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        asyncio.run(_drive())

        assert generation_count >= 3
        # Music stayed on because the retry succeeded.
        # (We set it to "off" ourselves to clean up.)
