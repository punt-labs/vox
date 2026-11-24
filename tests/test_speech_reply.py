"""Tests for provider advisories on speech replies."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from punt_vox.types_synthesis import DEFAULT_RATE, SynthesisSpec
from punt_vox.voxd.speech_reply import SpeechReply


@pytest.mark.asyncio
async def test_audio_advisory_preserves_payload_and_stamps_id() -> None:
    ws = MagicMock()
    ws.send_json = AsyncMock()
    payload: dict[str, object] = {"id": "ignored", "type": "audio", "cached": True}

    await SpeechReply(ws, "r1").audio(
        SynthesisSpec(provider="ElevenLabs", rate=120), payload
    )

    ws.send_json.assert_awaited_once_with(
        {
            "id": "r1",
            "type": "audio",
            "cached": True,
            "warning": "ElevenLabs ignores the requested speech rate (120%).",
        }
    )
    assert "warning" not in payload


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "rate"),
    [("elevenlabs", DEFAULT_RATE), ("elevenlabs", None), ("openai", 120)],
)
async def test_audio_reply_omits_inapplicable_advisory(
    provider: str, rate: int | None
) -> None:
    ws = MagicMock()
    ws.send_json = AsyncMock()

    await SpeechReply(ws, "r1").audio(
        SynthesisSpec(provider=provider, rate=rate), {"type": "playing"}
    )

    ws.send_json.assert_awaited_once_with({"id": "r1", "type": "playing"})
