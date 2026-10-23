"""Tests for punt_vox.providers.elevenlabs_stt."""

from __future__ import annotations

import ssl
import threading
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
from elevenlabs.core import ApiError
from elevenlabs.types import (
    SpeechToTextChunkResponseModel,
    SpeechToTextWebhookResponseModel,
    SpeechToTextWordResponseModel,
)

from punt_vox.providers.ca_bundle import CaBundleError
from punt_vox.providers.elevenlabs_stt import ElevenLabsSTTProvider
from punt_vox.providers.transport_faults import ProviderTransportError
from punt_vox.types_provider_errors import ProviderAuthError
from punt_vox.voxd.conversation_mode.audio_chunk import AudioChunk


def _word(logprob: float) -> SpeechToTextWordResponseModel:
    return SpeechToTextWordResponseModel(text="w", type="word", logprob=logprob)


def _spacing(logprob: float = 0.0) -> SpeechToTextWordResponseModel:
    """A ``type="spacing"`` entry -- interleaved between words by the real SDK."""
    return SpeechToTextWordResponseModel(text=" ", type="spacing", logprob=logprob)


def _response(
    text: str, words: list[SpeechToTextWordResponseModel]
) -> SpeechToTextChunkResponseModel:
    return SpeechToTextChunkResponseModel(
        language_code="eng", language_probability=1.0, text=text, words=words
    )


async def _chunks(*pcm: bytes) -> AsyncIterator[AudioChunk]:
    for payload in pcm:
        yield AudioChunk(pcm=payload, duration_s=0.02)


@pytest.fixture
def mock_stt_client() -> MagicMock:
    client = MagicMock()
    client.speech_to_text.convert.side_effect = lambda **kwargs: _response(  # pyright: ignore[reportUnknownLambdaType]
        "turn on the lights", [_word(-0.01), _word(-0.02), _word(-0.01)]
    )
    return client


class TestElevenLabsSTTProviderName:
    def test_name(self, mock_stt_client: MagicMock) -> None:
        provider = ElevenLabsSTTProvider(client=mock_stt_client)
        assert provider.name == "elevenlabs"


class TestElevenLabsSTTProviderTranscribeSuccess:
    async def test_transcribe_returns_one_final_event(
        self, mock_stt_client: MagicMock
    ) -> None:
        provider = ElevenLabsSTTProvider(client=mock_stt_client)
        events = [
            event async for event in provider.transcribe(_chunks(b"\x01\x00" * 100))
        ]
        (event,) = events
        assert event.is_final
        assert event.text == "turn on the lights"

    async def test_high_confidence_words_yield_high_confidence(
        self, mock_stt_client: MagicMock
    ) -> None:
        provider = ElevenLabsSTTProvider(client=mock_stt_client)
        (event,) = [
            event async for event in provider.transcribe(_chunks(b"\x01\x00" * 100))
        ]
        # exp(mean([-0.01, -0.02, -0.01])) is close to 1.0 -- a clean, unambiguous
        # transcript should never be gated by FR-19's ask-to-repeat floor.
        assert event.confidence > 0.9

    async def test_convert_receives_the_concatenated_pcm(
        self, mock_stt_client: MagicMock
    ) -> None:
        provider = ElevenLabsSTTProvider(client=mock_stt_client)
        async for _ in provider.transcribe(_chunks(b"\x01\x00", b"\x02\x00")):
            pass
        call_kwargs = mock_stt_client.speech_to_text.convert.call_args.kwargs
        assert call_kwargs["file"][1].getvalue() == b"\x01\x00\x02\x00"
        assert call_kwargs["file_format"] == "pcm_s16le_16"

    async def test_model_override_reaches_the_sdk_call(
        self, mock_stt_client: MagicMock
    ) -> None:
        provider = ElevenLabsSTTProvider(model="scribe_v2", client=mock_stt_client)
        async for _ in provider.transcribe(_chunks(b"\x01\x00")):
            pass
        call_kwargs = mock_stt_client.speech_to_text.convert.call_args.kwargs
        assert call_kwargs["model_id"] == "scribe_v2"


class TestElevenLabsSTTProviderAmbiguousCapture:
    """FR-19: never fabricate on ambiguous or failed capture."""

    async def test_empty_audio_reports_zero_confidence(
        self, mock_stt_client: MagicMock
    ) -> None:
        provider = ElevenLabsSTTProvider(client=mock_stt_client)
        (event,) = [event async for event in provider.transcribe(_chunks())]
        assert event.is_final
        assert event.confidence == 0.0
        assert event.text == ""
        mock_stt_client.speech_to_text.convert.assert_not_called()

    async def test_no_words_in_response_reports_zero_confidence(self) -> None:
        client = MagicMock()
        client.speech_to_text.convert.side_effect = lambda **kwargs: _response(  # pyright: ignore[reportUnknownLambdaType]
            "", []
        )
        provider = ElevenLabsSTTProvider(client=client)
        (event,) = [
            event async for event in provider.transcribe(_chunks(b"\x01\x00" * 100))
        ]
        assert event.confidence == 0.0

    async def test_interleaved_spacing_entries_do_not_move_the_score(self) -> None:
        """Averaging non-word entries would inflate confidence toward 1.0.

        The real SDK interleaves ``type="spacing"`` entries between words
        with near-deterministic (near-zero) logprob; those must be excluded
        from the average, or a genuinely mediocre transcript's low-logprob
        words get diluted by the always-near-zero spacing entries and the
        reported confidence lands artificially high.
        """
        client = MagicMock()
        low_confidence_words = [_word(-6.0), _word(-5.5)]
        client.speech_to_text.convert.side_effect = lambda **kwargs: _response(  # pyright: ignore[reportUnknownLambdaType]
            "unintelligible mumble", low_confidence_words
        )
        provider_without_spacing = ElevenLabsSTTProvider(client=client)
        (event_without_spacing,) = [
            event
            async for event in provider_without_spacing.transcribe(
                _chunks(b"\x01\x00" * 100)
            )
        ]

        interleaved = [
            _spacing(),
            low_confidence_words[0],
            _spacing(),
            low_confidence_words[1],
            _spacing(),
        ]

        def _interleaved_response(
            **kwargs: object,
        ) -> SpeechToTextChunkResponseModel:
            return _response("unintelligible mumble", interleaved)

        client_with_spacing = MagicMock()
        client_with_spacing.speech_to_text.convert.side_effect = _interleaved_response
        provider_with_spacing = ElevenLabsSTTProvider(client=client_with_spacing)
        (event_with_spacing,) = [
            event
            async for event in provider_with_spacing.transcribe(
                _chunks(b"\x01\x00" * 100)
            )
        ]

        assert event_with_spacing.confidence == event_without_spacing.confidence
        # And it must stay low, not get pulled toward the spacing entries'
        # near-zero logprob (which would round-trip to a confidence near 1.0).
        assert event_with_spacing.confidence < 0.6

    async def test_all_spacing_no_words_reports_zero_confidence(self) -> None:
        client = MagicMock()
        client.speech_to_text.convert.side_effect = lambda **kwargs: _response(  # pyright: ignore[reportUnknownLambdaType]
            "", [_spacing(), _spacing()]
        )
        provider = ElevenLabsSTTProvider(client=client)
        (event,) = [
            event async for event in provider.transcribe(_chunks(b"\x01\x00" * 100))
        ]
        assert event.confidence == 0.0

    async def test_low_confidence_words_report_low_confidence(self) -> None:
        client = MagicMock()
        client.speech_to_text.convert.side_effect = lambda **kwargs: _response(  # pyright: ignore[reportUnknownLambdaType]
            "unintelligible mumble", [_word(-6.0), _word(-5.5)]
        )
        provider = ElevenLabsSTTProvider(client=client)
        (event,) = [
            event async for event in provider.transcribe(_chunks(b"\x01\x00" * 100))
        ]
        assert event.confidence < 0.6

    async def test_unexpected_response_shape_reports_zero_confidence(self) -> None:
        """An unexpected SDK response variant is treated as ambiguous, not fatal."""
        client = MagicMock()

        def _webhook_shape(**kwargs: object) -> SpeechToTextWebhookResponseModel:
            return SpeechToTextWebhookResponseModel(
                message="queued", request_id="req-1"
            )

        client.speech_to_text.convert.side_effect = _webhook_shape
        provider = ElevenLabsSTTProvider(client=client)
        (event,) = [
            event async for event in provider.transcribe(_chunks(b"\x01\x00" * 100))
        ]
        assert event.confidence == 0.0
        assert event.text == ""


class TestElevenLabsSTTProviderAuthFailure:
    async def test_401_raises_provider_auth_error(self) -> None:
        client = MagicMock()
        client.speech_to_text.convert.side_effect = ApiError(status_code=401)
        provider = ElevenLabsSTTProvider(client=client)
        with pytest.raises(ProviderAuthError):
            async for _ in provider.transcribe(_chunks(b"\x01\x00" * 100)):
                pass

    async def test_non_401_api_error_propagates_unchanged(self) -> None:
        client = MagicMock()
        client.speech_to_text.convert.side_effect = ApiError(status_code=500)
        provider = ElevenLabsSTTProvider(client=client)
        with pytest.raises(ApiError) as exc_info:
            async for _ in provider.transcribe(_chunks(b"\x01\x00" * 100)):
                pass
        assert exc_info.value.status_code == 500


class TestElevenLabsSTTProviderCheckHealth:
    def test_reports_unset_key(
        self, mock_stt_client: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
        provider = ElevenLabsSTTProvider(client=mock_stt_client)

        (check,) = provider.check_health()

        assert check.passed is False

    def test_reports_set_key(
        self, mock_stt_client: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_test")
        provider = ElevenLabsSTTProvider(client=mock_stt_client)

        (check,) = provider.check_health()

        assert check.passed is True


class TestElevenLabsSTTProviderBadCaBundle:
    """An unloadable ``SSL_CERT_FILE`` fails the transcription, by name."""

    @pytest.fixture
    def missing_ca(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        cafile = tmp_path / "gone" / "ca-bundle.pem"
        monkeypatch.setenv("SSL_CERT_FILE", str(cafile))
        monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
        return cafile

    def test_construction_succeeds(self, missing_ca: Path) -> None:
        provider = ElevenLabsSTTProvider()

        assert provider.name == "elevenlabs"

    async def test_transcribe_names_the_bundle(self, missing_ca: Path) -> None:
        provider = ElevenLabsSTTProvider()

        with pytest.raises(CaBundleError) as info:
            async for _ in provider.transcribe(_chunks(b"\x01\x00" * 100)):
                pass

        assert "SSL_CERT_FILE" in str(info.value)
        assert str(missing_ca) not in str(info.value)


class TestElevenLabsSTTProviderOffTheLoop:
    """The first transcription builds the SDK client, which reads the CA bundle
    from disk -- blocking I/O that must not stall the call's event loop.
    """

    async def test_first_sdk_build_runs_off_the_event_loop(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        loop_thread = threading.get_ident()
        build_threads: list[int] = []

        def _fake_sdk(**_kwargs: object) -> MagicMock:
            build_threads.append(threading.get_ident())
            client = MagicMock()
            client.speech_to_text.convert.return_value = _response("hi", [_word(0.0)])
            return client

        monkeypatch.setattr(
            "punt_vox.providers.elevenlabs_client.ElevenLabs", _fake_sdk
        )
        provider = ElevenLabsSTTProvider()

        async for _ in provider.transcribe(_chunks(b"\x01\x00" * 100)):
            pass

        assert len(build_threads) == 1
        assert build_threads[0] != loop_thread


class TestElevenLabsSTTProviderTransportFailure:
    async def test_transport_error_is_transient_and_chained(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SSL_CERT_FILE", raising=False)
        cause = httpx.ConnectError("refused")
        client = MagicMock()
        client.speech_to_text.convert.side_effect = cause
        provider = ElevenLabsSTTProvider(client=client)

        with pytest.raises(ProviderTransportError) as info:
            async for _ in provider.transcribe(_chunks(b"\x01\x00" * 100)):
                pass

        assert info.value.__cause__ is cause

    async def test_tls_verify_failure_with_bundle_set_names_the_variable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SSL_CERT_FILE", "/etc/private/ca.pem")
        error = httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED]")
        error.__cause__ = ssl.SSLCertVerificationError(1, "verify failed")
        client = MagicMock()
        client.speech_to_text.convert.side_effect = error
        provider = ElevenLabsSTTProvider(client=client)

        with pytest.raises(CaBundleError, match="SSL_CERT_FILE"):
            async for _ in provider.transcribe(_chunks(b"\x01\x00" * 100)):
                pass
