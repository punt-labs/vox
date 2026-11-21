"""Exercise the real macOS Say binary and local transcription together."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from punt_vox.providers.say import SayProvider
from roundtrip.backends import WhisperModel, whisper_transcript
from roundtrip.oracle import assert_transcript, decode, synthesize
from roundtrip.test_offline import CORPUS, REFERENCES

pytestmark = [
    pytest.mark.roundtrip,
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform != "darwin", reason="requires macOS Say"),
]


@pytest.mark.parametrize(
    ("text", "expected"),
    list(zip(CORPUS, REFERENCES, strict=True)),
    ids=["garden", "river", "identifiers", "chunks"],
)
async def test_say_roundtrip(
    text: str, expected: str, tmp_path: Path, whisper_model: WhisperModel
) -> None:
    path = synthesize(SayProvider(), text, tmp_path)
    decode(path)
    actual = await whisper_transcript(whisper_model, path)
    error = assert_transcript(expected, actual, 0.05)
    print(f"say/whisper WER={error:.3f}: {text[:45]}")
