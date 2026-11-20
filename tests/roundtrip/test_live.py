"""Credentialed provider matrix, strict in the nightly workflow."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from punt_vox.normalize import normalize_for_speech
from punt_vox.providers.elevenlabs import ElevenLabsProvider
from punt_vox.providers.openai import OpenAIProvider
from punt_vox.providers.polly import PollyProvider
from punt_vox.types import TTSProvider
from roundtrip.backends import transcriber
from roundtrip.oracle import assert_transcript, decode, synthesize
from roundtrip.test_offline import CORPUS

pytestmark = [pytest.mark.roundtrip, pytest.mark.integration]


def require_credentials(provider: str, backend: str) -> None:
    required = {
        "elevenlabs": ["ELEVENLABS_API_KEY"],
        "openai": ["OPENAI_API_KEY"],
        "polly": ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_DEFAULT_REGION"],
    }[provider]
    if backend == "scribe":
        required = [*required, "ELEVENLABS_API_KEY"]
    missing = sorted({name for name in required if not os.environ.get(name)})
    if missing:
        reason = "roundtrip prerequisite missing: " + ", ".join(missing)
        if os.environ.get("VOX_ROUNDTRIP_REQUIRE_LIVE") == "1":
            pytest.fail(reason)
        pytest.skip(reason)


@pytest.mark.parametrize("provider", ["elevenlabs", "openai", "polly"])
@pytest.mark.parametrize("backend", ["scribe", "whisper"])
@pytest.mark.parametrize(
    "text", CORPUS, ids=["garden", "river", "identifiers", "chunks"]
)
async def test_live_roundtrip(
    provider: str, backend: str, text: str, tmp_path: Path
) -> None:
    require_credentials(provider, backend)
    factory: dict[str, type[TTSProvider]] = {
        "elevenlabs": ElevenLabsProvider,
        "openai": OpenAIProvider,
        "polly": PollyProvider,
    }
    path = synthesize(factory[provider](), text, tmp_path)
    decode(path)
    actual = await transcriber(backend)(path)
    error = assert_transcript(normalize_for_speech(text), actual, 0.25)
    print(f"{provider}/{backend} WER={error:.3f}: {text[:45]}")
