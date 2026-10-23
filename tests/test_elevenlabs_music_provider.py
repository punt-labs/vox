"""Tests for punt_vox.providers.elevenlabs_music."""

from __future__ import annotations

import logging
import ssl
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import httpx
import pytest
from elevenlabs.core import ApiError  # pyright: ignore[reportMissingTypeStubs]

from punt_vox.providers.elevenlabs_music import ElevenLabsMusicProvider
from punt_vox.providers.provider_faults import ProviderTransportError
from punt_vox.providers.provider_status import ProviderStatusError
from punt_vox.types import MusicProvider
from punt_vox.voxd.programs.music_producer import LengthPolicy, MusicProducer
from punt_vox.voxd.programs.part_tags import PartTags
from punt_vox.voxd.programs.producer import (
    PartSpec,
    ProducerBadInputError,
    ProducerTransientError,
)
from punt_vox.voxd.synthesis import (
    _api_key_context,  # pyright: ignore[reportPrivateUsage]
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _identity(sdk: object) -> object:
    """Return the SDK itself -- builds it, through the client's one door."""
    return sdk


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

        with pytest.raises(ProviderStatusError):
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

        with pytest.raises(ProviderStatusError):
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

        with caplog.at_level(logging.ERROR), pytest.raises(ProviderStatusError):
            await provider.generate_track("test", 60_000, out)

        assert "music API call failed" in caplog.text


# ---------------------------------------------------------------------------
# Constructor — the SDK client is deferred to first use
# ---------------------------------------------------------------------------


class TestConstructor:
    def test_accepts_injected_client(self, mock_music_client: MagicMock) -> None:
        provider = ElevenLabsMusicProvider(client=mock_music_client)
        assert provider._client.call(_identity) is mock_music_client  # pyright: ignore[reportPrivateUsage]

    def test_construction_does_not_build_the_sdk(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The daemon builds this provider at boot; the SDK must wait for music."""
        built: list[dict[str, Any]] = []

        def fake_elevenlabs(**kwargs: Any) -> MagicMock:  # pyright: ignore[reportExplicitAny]
            built.append(kwargs)
            return MagicMock()

        monkeypatch.setattr(
            "punt_vox.providers.elevenlabs_client.ElevenLabs", fake_elevenlabs
        )

        ElevenLabsMusicProvider()

        assert built == []

    @pytest.mark.asyncio
    async def test_first_generation_builds_the_sdk_with_the_env_key(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key-123")
        captured: dict[str, Any] = {}
        client = MagicMock()
        client.music.stream.side_effect = (
            lambda **kwargs: _make_music_stream_response()  # pyright: ignore[reportUnknownLambdaType]
        )

        def fake_elevenlabs(**kwargs: Any) -> MagicMock:  # pyright: ignore[reportExplicitAny]
            captured.update(kwargs)
            return client

        monkeypatch.setattr(
            "punt_vox.providers.elevenlabs_client.ElevenLabs", fake_elevenlabs
        )
        provider = ElevenLabsMusicProvider()

        await provider.generate_track("ambient", 60_000, tmp_path / "t.mp3")

        assert captured.get("api_key") == "test-key-123"

    @pytest.mark.asyncio
    async def test_a_per_call_key_never_reaches_the_long_lived_client(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The music client outlives every synthesis request, so it keeps the
        daemon's own key even when its first build lands inside a per-call
        ``api_key`` context that has swapped the environment.
        """
        monkeypatch.setenv("ELEVENLABS_API_KEY", "daemon-key-A")
        captured: dict[str, Any] = {}
        client = MagicMock()
        client.music.stream.side_effect = (
            lambda **kwargs: _make_music_stream_response()  # pyright: ignore[reportUnknownLambdaType]
        )

        def fake_elevenlabs(**kwargs: Any) -> MagicMock:  # pyright: ignore[reportExplicitAny]
            captured.update(kwargs)
            return client

        monkeypatch.setattr(
            "punt_vox.providers.elevenlabs_client.ElevenLabs", fake_elevenlabs
        )
        provider = ElevenLabsMusicProvider()

        with _api_key_context("caller-key-B", "elevenlabs"):
            await provider.generate_track("ambient", 60_000, tmp_path / "t.mp3")

        assert captured.get("api_key") == "daemon-key-A"

    @pytest.mark.asyncio
    async def test_an_explicit_key_wins_over_the_environment(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("ELEVENLABS_API_KEY", "env-key")
        captured: dict[str, Any] = {}
        client = MagicMock()
        client.music.stream.side_effect = (
            lambda **kwargs: _make_music_stream_response()  # pyright: ignore[reportUnknownLambdaType]
        )

        def fake_elevenlabs(**kwargs: Any) -> MagicMock:  # pyright: ignore[reportExplicitAny]
            captured.update(kwargs)
            return client

        monkeypatch.setattr(
            "punt_vox.providers.elevenlabs_client.ElevenLabs", fake_elevenlabs
        )
        provider = ElevenLabsMusicProvider(api_key="explicit-key")

        await provider.generate_track("ambient", 60_000, tmp_path / "t.mp3")

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
    async def test_info_logs_prompt_digest_not_text(
        self,
        music_provider: ElevenLabsMusicProvider,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """INFO carries the prompt's length and hash, never its agent-authored text."""
        import hashlib

        out = tmp_path / "logged_prompt.mp3"
        prompt = "Klezmer, clarinet lead. freylekh at 120 BPM in D freygish"
        with caplog.at_level(logging.INFO):
            await music_provider.generate_track(prompt, 120_000, out)

        info = "\n".join(
            r.getMessage() for r in caplog.records if r.levelno == logging.INFO
        )
        assert prompt not in info
        assert f"prompt_len={len(prompt)}" in info
        assert hashlib.sha256(prompt.encode()).hexdigest()[:12] in info
        assert "logged_prompt.mp3" in info

    @pytest.mark.asyncio
    async def test_debug_logs_full_prompt(
        self,
        music_provider: ElevenLabsMusicProvider,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """The full prompt stays at DEBUG -- below the INFO durable-file threshold."""
        out = tmp_path / "debug_prompt.mp3"
        prompt = "Ambient drone, evolving pads, no percussion"
        with caplog.at_level(logging.DEBUG):
            await music_provider.generate_track(prompt, 120_000, out)

        debug = "\n".join(
            r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG
        )
        assert prompt in debug

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


# ---------------------------------------------------------------------------
# Failures raised while iterating the SDK's lazy stream
# ---------------------------------------------------------------------------


def _raising_stream(error: Exception, *chunks: bytes) -> Iterator[bytes]:
    """Mimic the SDK's lazy ``stream()``: nothing happens until iterated."""
    yield from chunks
    raise error


def _client_failing_with(error: Exception, *chunks: bytes) -> MagicMock:
    client = MagicMock()
    client.music.stream.side_effect = (
        lambda **_kwargs: _raising_stream(error, *chunks)  # pyright: ignore[reportUnknownLambdaType]
    )
    return client


class TestStreamFailures:
    @pytest.mark.asyncio
    async def test_api_error_on_first_chunk_is_logged_and_raised(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        provider = ElevenLabsMusicProvider(
            client=_client_failing_with(ApiError(status_code=422))
        )

        with caplog.at_level(logging.ERROR), pytest.raises(ProviderStatusError):
            await provider.generate_track("test", 60_000, tmp_path / "t.mp3")

        assert "music API call failed" in caplog.text

    @pytest.mark.asyncio
    async def test_mid_stream_failure_leaves_no_partial_file(
        self, tmp_path: Path
    ) -> None:
        provider = ElevenLabsMusicProvider(
            client=_client_failing_with(httpx.ReadError("dropped"), b"\xff" * 64)
        )

        with pytest.raises(ProviderTransportError):
            await provider.generate_track("test", 60_000, tmp_path / "t.mp3")

        assert list(tmp_path.iterdir()) == []

    @pytest.mark.asyncio
    async def test_transport_error_is_transient_to_the_producer(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SSL_CERT_FILE", raising=False)
        provider = ElevenLabsMusicProvider(
            client=_client_failing_with(httpx.ConnectError("refused"))
        )
        producer = MusicProducer(provider, LengthPolicy())

        with pytest.raises(ProducerTransientError):
            await producer.produce(_spec(), tmp_path / "t.mp3")

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("status", "expected"),
        [(422, ProducerBadInputError), (429, ProducerTransientError)],
        ids=["bad-prompt", "rate-limited"],
    )
    async def test_an_answered_status_reaches_the_producer_classified(
        self,
        tmp_path: Path,
        status: int,
        expected: type[Exception],
    ) -> None:
        """The real provider's translated error routes by status in the producer
        exactly as the SDK's ApiError did."""
        body = {"detail": {"message": "server says no"}}
        provider = ElevenLabsMusicProvider(
            client=_client_failing_with(ApiError(status_code=status, body=body))
        )
        producer = MusicProducer(provider, LengthPolicy())

        with pytest.raises(expected, match="server says no"):
            await producer.produce(_spec(), tmp_path / "t.mp3")

    @pytest.mark.asyncio
    async def test_tls_verify_failure_with_bundle_set_is_permanent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SSL_CERT_FILE", "/etc/private/ca.pem")
        error = httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED]")
        error.__cause__ = ssl.SSLCertVerificationError(1, "verify failed")
        producer = MusicProducer(
            ElevenLabsMusicProvider(client=_client_failing_with(error)), LengthPolicy()
        )

        with pytest.raises(ProducerBadInputError, match="SSL_CERT_FILE"):
            await producer.produce(_spec(), tmp_path / "t.mp3")


def _spec() -> PartSpec:
    tags = PartTags(title="calm", album="mix", genre="ambient", index=1, total=12)
    return PartSpec(prompt="calm", index=1, tags=tags)
