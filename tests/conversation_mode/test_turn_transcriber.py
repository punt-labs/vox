"""Tests for :class:`~.turn_transcriber.TurnTranscriber`."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from conversation_mode._stt_fakes import FakeSTTProvider
from punt_vox.providers.ca_bundle import CaBundleError
from punt_vox.providers.transport_faults import ProviderTransportError
from punt_vox.types import HealthCheck
from punt_vox.types_provider_errors import ProviderAuthError
from punt_vox.voxd.conversation_mode.audio_chunk import AudioChunk
from punt_vox.voxd.conversation_mode.stt_provider import TranscriptEvent
from punt_vox.voxd.conversation_mode.turn_transcriber import (
    CONFIDENCE_FLOOR,
    TurnTranscriber,
)


class _SpyTurnTimer:
    def __init__(self) -> None:
        self.marks: list[tuple[str, str | None]] = []

    def mark(self, stage: str, *, detail: str | None = None) -> None:
        self.marks.append((stage, detail))


def _one_chunk() -> list[AudioChunk]:
    return [AudioChunk(pcm=b"\x00\x00", duration_s=0.02)]


async def test_a_high_confidence_final_event_is_returned() -> None:
    timer = _SpyTurnTimer()
    stt = FakeSTTProvider(
        [TranscriptEvent(text="hello", confidence=0.95, is_final=True)]
    )
    transcriber = TurnTranscriber(stt, timer)

    result = await transcriber.transcribe(AudioChunk.as_async_iter(_one_chunk()))

    assert result is not None
    assert result.text == "hello"
    assert dict(timer.marks)["stt_response_received"] == "confidence=0.95"


async def test_a_low_confidence_final_event_returns_none() -> None:
    """FR-19: below the confidence floor, never fabricate."""
    timer = _SpyTurnTimer()
    stt = FakeSTTProvider(
        [
            TranscriptEvent(
                text="unsure", confidence=CONFIDENCE_FLOOR - 0.1, is_final=True
            )
        ]
    )
    transcriber = TurnTranscriber(stt, timer)

    result = await transcriber.transcribe(AudioChunk.as_async_iter(_one_chunk()))

    assert result is None


async def test_a_trailing_non_final_event_returns_none() -> None:
    """A stream that ends on a partial result must not be mistaken for a
    settled transcript -- ``is_final`` marks the last event for a turn, and
    a stream ending without one means recognition never settled.
    """
    timer = _SpyTurnTimer()
    stt = FakeSTTProvider(
        [TranscriptEvent(text="partial", confidence=0.95, is_final=False)]
    )
    transcriber = TurnTranscriber(stt, timer)

    result = await transcriber.transcribe(AudioChunk.as_async_iter(_one_chunk()))

    assert result is None
    assert dict(timer.marks)["stt_response_received"] == "no transcript"


async def test_a_final_event_wins_over_an_earlier_non_final_event() -> None:
    timer = _SpyTurnTimer()
    stt = FakeSTTProvider(
        [
            TranscriptEvent(text="partial", confidence=0.30, is_final=False),
            TranscriptEvent(text="hello", confidence=0.95, is_final=True),
        ]
    )
    transcriber = TurnTranscriber(stt, timer)

    result = await transcriber.transcribe(AudioChunk.as_async_iter(_one_chunk()))

    assert result is not None
    assert result.text == "hello"


async def test_no_events_at_all_returns_none() -> None:
    timer = _SpyTurnTimer()
    stt = FakeSTTProvider([])
    transcriber = TurnTranscriber(stt, timer)

    result = await transcriber.transcribe(AudioChunk.as_async_iter(_one_chunk()))

    assert result is None
    assert dict(timer.marks)["stt_response_received"] == "no transcript"


async def test_a_transient_provider_failure_returns_none_not_raises() -> None:
    timer = _SpyTurnTimer()
    stt = FakeSTTProvider([], transcribe_error="rate limited")
    transcriber = TurnTranscriber(stt, timer)

    result = await transcriber.transcribe(AudioChunk.as_async_iter(_one_chunk()))

    assert result is None
    assert dict(timer.marks)["stt_response_received"] == "transcribe failed"


class _RaisingSTTProvider:
    """An ``STTProvider`` whose ``transcribe`` always raises *error*."""

    def __init__(self, error: Exception) -> None:
        # Raised through an instance attribute, so mypy cannot see that the
        # trailing ``yield`` is unreachable -- the method still has to be
        # shaped as an async generator to satisfy STTProvider.transcribe.
        self._error: Exception | None = error

    @property
    def name(self) -> str:
        return "raising-fake"

    async def transcribe(
        self, chunks: AsyncIterator[AudioChunk]
    ) -> AsyncIterator[TranscriptEvent]:
        async for _ in chunks:
            pass
        if self._error is not None:
            raise self._error
        yield TranscriptEvent(  # pragma: no cover -- unreachable
            text="", confidence=0.0, is_final=True
        )

    def check_health(self) -> list[HealthCheck]:
        return []


async def test_a_provider_auth_error_is_reraised_not_swallowed() -> None:
    """A revoked/expired STT credential is certain and permanent, unlike a
    transient fault -- it must propagate to the caller so the call can end
    with an actionable message, never get laundered into the same ``None``
    ambiguous-audio recovers to.
    """
    timer = _SpyTurnTimer()
    transcriber = TurnTranscriber(
        _RaisingSTTProvider(ProviderAuthError("elevenlabs", 401)), timer
    )

    with pytest.raises(ProviderAuthError):
        await transcriber.transcribe(AudioChunk.as_async_iter(_one_chunk()))

    assert dict(timer.marks)["stt_response_received"] == "STT auth failed"


def _bad_bundle() -> CaBundleError:
    return CaBundleError(
        "SSL_CERT_FILE names a CA bundle that cannot be loaded "
        "(No such file or directory); fix the path or unset SSL_CERT_FILE"
    )


async def test_an_unloadable_ca_bundle_is_reraised_not_swallowed() -> None:
    """A bad SSL_CERT_FILE is configuration: every later turn fails the same
    way, so it must reach the caller, never collapse into "ask to repeat".
    """
    timer = _SpyTurnTimer()
    transcriber = TurnTranscriber(_RaisingSTTProvider(_bad_bundle()), timer)

    with pytest.raises(CaBundleError):
        await transcriber.transcribe(AudioChunk.as_async_iter(_one_chunk()))

    assert dict(timer.marks)["stt_response_received"] == "STT TLS setup failed"


async def test_a_provider_transport_error_still_asks_to_repeat() -> None:
    """A dropped connection is transient: the next turn may succeed."""
    timer = _SpyTurnTimer()
    error = ProviderTransportError("provider request failed in transport")
    transcriber = TurnTranscriber(_RaisingSTTProvider(error), timer)

    assert await transcriber.transcribe(AudioChunk.as_async_iter(_one_chunk())) is None
