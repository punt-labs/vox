"""Offline means no provider API; the initial Whisper model download needs network."""

from __future__ import annotations

from pathlib import Path

import pytest

from punt_vox.core import split_text
from punt_vox.normalize import normalize_for_speech
from punt_vox.providers.espeak import EspeakProvider
from roundtrip import oracle
from roundtrip.backends import WhisperModel, whisper_transcript
from roundtrip.oracle import (
    THRESHOLD,
    assert_transcript,
    decode,
    synthesize,
    word_error_rate,
    words,
)

pytestmark = pytest.mark.roundtrip

CORPUS = [
    "The garden gate is open. Please bring the flowers inside.",
    "A quiet river flows through the valley. The birds sing in the morning.",
    "Read the config_file and check the repoPath. Write the result to stdout.",
    (
        "The old library stands beside the river. Every morning the librarian opens "
        "the wooden doors and welcomes the readers. A young student walks through "
        "the garden carrying a book about birds. Inside the building the rooms are "
        "warm and quiet. Sunlight falls across the tables while people read their "
        "favorite stories. Later in the afternoon everyone gathers near the window "
        "to watch the rain. When the sky clears they return home along the river."
    ),
]

REFERENCES = [
    CORPUS[0],
    CORPUS[1],
    (
        "Read the configuration file and check the repository path. "
        "Write the result to standard output."
    ),
    CORPUS[3],
]


@pytest.mark.parametrize(
    ("text", "expected"),
    list(zip(CORPUS, REFERENCES, strict=True)),
    ids=["garden", "river", "identifiers", "chunks"],
)
async def test_offline_roundtrip(
    text: str, expected: str, tmp_path: Path, whisper_model: WhisperModel
) -> None:
    if text == CORPUS[-1]:
        assert len(split_text(normalize_for_speech(text), 240)) > 1
    path = synthesize(EspeakProvider(), text, tmp_path)
    decode(path)
    actual = await whisper_transcript(whisper_model, path)
    assert 1 / min(len(words(ref)) for ref in REFERENCES) > THRESHOLD
    error = assert_transcript(expected, actual, THRESHOLD)
    print(f"offline WER={error:.4f} limit={THRESHOLD}: {text[:45]}")


@pytest.mark.parametrize(
    "fault", ["dropped-word", "swapped-word", "normalizer-identity"]
)
async def test_injected_fault_is_rejected(
    fault: str,
    tmp_path: Path,
    whisper_model: WhisperModel,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = REFERENCES[0]
    if fault == "dropped-word":
        text = CORPUS[0].replace("garden ", "")
    elif fault == "swapped-word":
        text = CORPUS[0].replace("garden", "wooden")
    else:
        text, expected = CORPUS[2], REFERENCES[2]

        def identity(text: str) -> str:
            return text

        monkeypatch.setattr(oracle, "normalize_for_speech", identity)
    path = synthesize(EspeakProvider(), text, tmp_path)
    decode(path)
    actual = await whisper_transcript(whisper_model, path)
    error = word_error_rate(expected, actual)
    print(f"{fault} WER={error:.4f} limit={THRESHOLD}: {actual}")
    with pytest.raises(AssertionError, match="WER="):
        assert_transcript(expected, actual, THRESHOLD)


async def test_wrong_speech_is_rejected(
    tmp_path: Path, whisper_model: WhisperModel
) -> None:
    path = synthesize(EspeakProvider(), CORPUS[0], tmp_path)
    decode(path)
    actual = await whisper_transcript(whisper_model, path)
    expected = "Purple elephants dance beneath distant silver stars."
    print(
        f"negative WER={word_error_rate(expected, actual):.3f} "
        f"(must exceed {THRESHOLD})"
    )
    with pytest.raises(AssertionError, match="WER="):
        assert_transcript(expected, actual, THRESHOLD)
