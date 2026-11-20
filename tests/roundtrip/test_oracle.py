"""Fast unit checks for the metric, empty input, and silent audio rejection."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydub import AudioSegment

from roundtrip.backends import transcriber
from roundtrip.oracle import assert_transcript, decode, word_error_rate
from roundtrip.test_live import require_credentials


@pytest.mark.parametrize(
    ("reference", "actual", "expected"),
    [
        ("Hello, WORLD!", " hello world ", 0.0),
        ("a b c", "a c", 1 / 3),
        ("a b", "a b c", 0.5),
        ("a b", "a c", 0.5),
        ("", "", 0.0),
        ("", "extra", 1.0),
        ("hello", "", 1.0),
    ],
)
def test_wer(reference: str, actual: str, expected: float) -> None:
    assert word_error_rate(reference, actual) == expected


def test_empty_reference_is_rejected() -> None:
    with pytest.raises(AssertionError, match="nonempty reference"):
        assert_transcript("", "", 0.25)


def test_silent_audio_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "silent.wav"
    AudioSegment.silent(duration=100).export(str(path), format="wav")
    with pytest.raises(AssertionError, match="silent"):
        decode(path)


def test_empty_audio_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "empty.wav"
    AudioSegment.silent(duration=0).export(str(path), format="wav")
    with pytest.raises(AssertionError, match="zero duration"):
        decode(path)


def test_unknown_backend_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown transcription backend"):
        transcriber("unknown")


@pytest.mark.parametrize("strict", [False, True])
def test_missing_live_prerequisite(
    monkeypatch: pytest.MonkeyPatch, strict: bool
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("VOX_ROUNDTRIP_REQUIRE_LIVE", "1" if strict else "0")
    error = pytest.fail.Exception if strict else pytest.skip.Exception
    with pytest.raises(error, match="roundtrip prerequisite missing: OPENAI_API_KEY"):
        require_credentials("openai", "whisper")


def test_live_prerequisites_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-placeholder")
    require_credentials("openai", "whisper")
