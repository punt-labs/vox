"""Tests for punt_vox.voxd observability and direct-play dispatch."""
# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import io
import logging
import os
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydub import AudioSegment  # pyright: ignore[reportMissingTypeStubs]

from punt_vox.paths import ensure_user_dirs
from punt_vox.voxd import (
    ChimeDedup,
    DaemonContext,
    DedupHit,
    OnceDedup,
    PlaybackItem,
    _apply_vibe_for_synthesis,
    _config_dir,
    _handle_synthesize,
    _health_payload_full,
    _health_payload_minimal,
    _health_route,
    _load_keys,
    _log_dir,
    _model_supports_expressive_tags,
    _play_audio,
    _run_dir,
    _try_direct_play,
)


def _make_ctx() -> DaemonContext:
    """Build a DaemonContext without touching real files or auth."""
    return DaemonContext(auth_token=None, port=0)


_VALID_MP3_BYTES: bytes | None = None


def _get_valid_mp3_bytes() -> bytes:
    """Return a cached slice of valid MP3 bytes.

    ``pydub``'s ``_pad_audio_file`` in core.py feeds the synthesized
    file to ffmpeg for silence-tail appending. ffmpeg rejects any file
    that is not valid MP3, so stub providers must write real audio.
    Generating 50ms of silence is cheap and cached.
    """
    global _VALID_MP3_BYTES
    if _VALID_MP3_BYTES is None:
        silence: Any = AudioSegment.silent(duration=50)  # pyright: ignore[reportUnknownMemberType]
        buf = io.BytesIO()
        silence.export(buf, format="mp3")  # pyright: ignore[reportUnknownMemberType]
        _VALID_MP3_BYTES = buf.getvalue()  # pyright: ignore[reportConstantRedefinition]
    return _VALID_MP3_BYTES


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
        def _cache_miss(_text: str, _voice: str, _provider: str) -> Path | None:
            return None

        def _cache_noop(_text: str, _voice: str, _provider: str, _path: Path) -> None:
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
