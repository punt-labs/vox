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
    _config_dir,
    _handle_synthesize,
    _health_payload_full,
    _health_payload_minimal,
    _health_route,
    _load_keys,
    _log_dir,
    _play_audio,
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
