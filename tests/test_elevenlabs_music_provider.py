"""Tests for punt_vox.providers.elevenlabs_music."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from elevenlabs.core import ApiError  # pyright: ignore[reportMissingTypeStubs]

from punt_vox.providers.elevenlabs_music import ElevenLabsMusicProvider
from punt_vox.types import MusicProvider

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_music_stream_response(
    chunks: list[bytes] | None = None,
) -> list[bytes]:
    """Return a list of byte chunks simulating client.music.stream()."""
    if chunks is not None:
        return chunks
    return [b"\xff\xfb\x90\x00" * 256, b"\xff\xfb\x90\x00" * 128]


@pytest.fixture
def mock_music_client() -> MagicMock:
    """Create a mock ElevenLabs client with music.stream configured."""
    client = MagicMock()
    client.music.stream.side_effect = (
        lambda **kwargs: _make_music_stream_response()  # pyright: ignore[reportUnknownLambdaType]
    )
    return client


@pytest.fixture
def music_provider(mock_music_client: MagicMock) -> ElevenLabsMusicProvider:
    """Create an ElevenLabsMusicProvider with a mocked client."""
    return ElevenLabsMusicProvider(client=mock_music_client)


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    def test_implements_music_provider(
        self, music_provider: ElevenLabsMusicProvider
    ) -> None:
        assert isinstance(music_provider, MusicProvider)


# ---------------------------------------------------------------------------
# generate_track — happy path
# ---------------------------------------------------------------------------


class TestGenerateTrack:
    @pytest.mark.asyncio
    async def test_writes_file_to_output_path(
        self,
        music_provider: ElevenLabsMusicProvider,
        tmp_path: Path,
    ) -> None:
        out = tmp_path / "track.mp3"
        result = await music_provider.generate_track("chill beats", 120_000, out)

        assert result == out
        assert out.exists()
        assert out.stat().st_size > 0

    @pytest.mark.asyncio
    async def test_creates_parent_directories(
        self,
        music_provider: ElevenLabsMusicProvider,
        tmp_path: Path,
    ) -> None:
        out = tmp_path / "nested" / "deep" / "track.mp3"
        result = await music_provider.generate_track("lo-fi", 60_000, out)

        assert result == out
        assert out.exists()

    @pytest.mark.asyncio
    async def test_file_contains_all_streamed_bytes(
        self,
        mock_music_client: MagicMock,
        tmp_path: Path,
    ) -> None:
        chunk_a = b"\x01\x02\x03"
        chunk_b = b"\x04\x05"
        mock_music_client.music.stream.side_effect = (
            lambda **kwargs: [chunk_a, chunk_b]  # pyright: ignore[reportUnknownLambdaType]
        )
        provider = ElevenLabsMusicProvider(client=mock_music_client)
        out = tmp_path / "exact.mp3"

        await provider.generate_track("test", 10_000, out)

        assert out.read_bytes() == chunk_a + chunk_b

    @pytest.mark.asyncio
    async def test_returns_output_path(
        self,
        music_provider: ElevenLabsMusicProvider,
        tmp_path: Path,
    ) -> None:
        out = tmp_path / "result.mp3"
        result = await music_provider.generate_track("ambient", 120_000, out)
        assert result == out


# ---------------------------------------------------------------------------
# SDK call arguments
# ---------------------------------------------------------------------------


class TestSdkCallArguments:
    @pytest.mark.asyncio
    async def test_force_instrumental_always_true(
        self,
        mock_music_client: MagicMock,
        music_provider: ElevenLabsMusicProvider,
        tmp_path: Path,
    ) -> None:
        out = tmp_path / "instrumental.mp3"
        await music_provider.generate_track("rock", 120_000, out)

        call_kwargs = mock_music_client.music.stream.call_args.kwargs
        assert call_kwargs["force_instrumental"] is True

    @pytest.mark.asyncio
    async def test_passes_prompt(
        self,
        mock_music_client: MagicMock,
        music_provider: ElevenLabsMusicProvider,
        tmp_path: Path,
    ) -> None:
        out = tmp_path / "prompt.mp3"
        await music_provider.generate_track("jazzy chill vibes", 120_000, out)

        call_kwargs = mock_music_client.music.stream.call_args.kwargs
        assert call_kwargs["prompt"] == "jazzy chill vibes"

    @pytest.mark.asyncio
    async def test_passes_duration_ms(
        self,
        mock_music_client: MagicMock,
        music_provider: ElevenLabsMusicProvider,
        tmp_path: Path,
    ) -> None:
        out = tmp_path / "duration.mp3"
        await music_provider.generate_track("beats", 90_000, out)

        call_kwargs = mock_music_client.music.stream.call_args.kwargs
        assert call_kwargs["music_length_ms"] == 90_000

    @pytest.mark.asyncio
    async def test_default_output_format(
        self,
        mock_music_client: MagicMock,
        music_provider: ElevenLabsMusicProvider,
        tmp_path: Path,
    ) -> None:
        out = tmp_path / "format.mp3"
        await music_provider.generate_track("ambient", 120_000, out)

        call_kwargs = mock_music_client.music.stream.call_args.kwargs
        assert call_kwargs["output_format"] == "mp3_44100_128"

    @pytest.mark.asyncio
    async def test_custom_output_format(
        self,
        mock_music_client: MagicMock,
        tmp_path: Path,
    ) -> None:
        provider = ElevenLabsMusicProvider(
            client=mock_music_client,
            output_format="mp3_22050_32",
        )
        out = tmp_path / "custom_fmt.mp3"
        await provider.generate_track("ambient", 120_000, out)

        call_kwargs = mock_music_client.music.stream.call_args.kwargs
        assert call_kwargs["output_format"] == "mp3_22050_32"


# ---------------------------------------------------------------------------
# Temp file cleanup
# ---------------------------------------------------------------------------


class TestTempFileCleanup:
    @pytest.mark.asyncio
    async def test_no_temp_files_after_success(
        self,
        music_provider: ElevenLabsMusicProvider,
        tmp_path: Path,
    ) -> None:
        out = tmp_path / "clean.mp3"
        await music_provider.generate_track("test", 60_000, out)

        remaining = list(tmp_path.iterdir())
        assert remaining == [out]

    @pytest.mark.asyncio
    async def test_temp_file_cleaned_on_api_error(
        self,
        mock_music_client: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_music_client.music.stream.side_effect = ApiError(status_code=500)
        provider = ElevenLabsMusicProvider(client=mock_music_client)
        out = tmp_path / "fail.mp3"

        with pytest.raises(ApiError):
            await provider.generate_track("test", 60_000, out)

        # No temp files left behind.
        assert not out.exists()
        tmp_files = [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
        assert tmp_files == []

    @pytest.mark.asyncio
    async def test_temp_file_cleaned_on_empty_response(
        self,
        mock_music_client: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_music_client.music.stream.side_effect = lambda **kwargs: []  # pyright: ignore[reportUnknownLambdaType]
        provider = ElevenLabsMusicProvider(client=mock_music_client)
        out = tmp_path / "empty.mp3"

        with pytest.raises(RuntimeError, match="no audio data"):
            await provider.generate_track("test", 60_000, out)

        assert not out.exists()
        tmp_files = [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
        assert tmp_files == []


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_api_error_propagates(
        self,
        mock_music_client: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_music_client.music.stream.side_effect = ApiError(status_code=429)
        provider = ElevenLabsMusicProvider(client=mock_music_client)
        out = tmp_path / "rate_limit.mp3"

        with pytest.raises(ApiError):
            await provider.generate_track("test", 60_000, out)

    @pytest.mark.asyncio
    async def test_empty_response_raises_runtime_error(
        self,
        mock_music_client: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_music_client.music.stream.side_effect = lambda **kwargs: []  # pyright: ignore[reportUnknownLambdaType]
        provider = ElevenLabsMusicProvider(client=mock_music_client)
        out = tmp_path / "empty.mp3"

        with pytest.raises(RuntimeError, match="no audio data"):
            await provider.generate_track("test", 60_000, out)

    @pytest.mark.asyncio
    async def test_api_error_logged(
        self,
        mock_music_client: MagicMock,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        mock_music_client.music.stream.side_effect = ApiError(status_code=503)
        provider = ElevenLabsMusicProvider(client=mock_music_client)
        out = tmp_path / "logged.mp3"

        with caplog.at_level(logging.ERROR), pytest.raises(ApiError):
            await provider.generate_track("test", 60_000, out)

        assert "music API call failed" in caplog.text


# ---------------------------------------------------------------------------
# Constructor — api_key handling
# ---------------------------------------------------------------------------


class TestConstructor:
    def test_accepts_injected_client(self, mock_music_client: MagicMock) -> None:
        provider = ElevenLabsMusicProvider(client=mock_music_client)
        assert provider._client is mock_music_client  # pyright: ignore[reportPrivateUsage]

    def test_api_key_fallback_to_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key-123")
        captured: dict[str, Any] = {}

        def fake_elevenlabs(**kwargs: Any) -> MagicMock:  # pyright: ignore[reportExplicitAny]
            captured.update(kwargs)
            return MagicMock()

        # ElevenLabs is imported lazily inside __init__; patch the SDK class.
        monkeypatch.setattr("elevenlabs.ElevenLabs", fake_elevenlabs)

        ElevenLabsMusicProvider()
        assert captured.get("api_key") == "test-key-123"

    def test_explicit_api_key_overrides_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ELEVENLABS_API_KEY", "env-key")
        captured: dict[str, Any] = {}

        def fake_elevenlabs(**kwargs: Any) -> MagicMock:  # pyright: ignore[reportExplicitAny]
            captured.update(kwargs)
            return MagicMock()

        monkeypatch.setattr("elevenlabs.ElevenLabs", fake_elevenlabs)

        ElevenLabsMusicProvider(api_key="explicit-key")
        assert captured.get("api_key") == "explicit-key"


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


class TestLogging:
    @pytest.mark.asyncio
    async def test_logs_generation_start(
        self,
        music_provider: ElevenLabsMusicProvider,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        out = tmp_path / "log_start.mp3"
        with caplog.at_level(logging.INFO):
            await music_provider.generate_track("test", 120_000, out)

        assert "Generating music track" in caplog.text
        assert "120000" in caplog.text

    @pytest.mark.asyncio
    async def test_logs_completion(
        self,
        music_provider: ElevenLabsMusicProvider,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        out = tmp_path / "log_done.mp3"
        with caplog.at_level(logging.INFO):
            await music_provider.generate_track("test", 60_000, out)

        assert "Wrote music track" in caplog.text
        assert "log_done.mp3" in caplog.text
