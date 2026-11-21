"""Fast rejection checks for captured-speech ordering."""

from __future__ import annotations

import pytest
from roundtrip.playback_capture import prerequisite
from roundtrip.test_playback_capture import (
    SEAM,
    assert_markers,
    assert_seam,
    elevenlabs_passage,
)


def test_ordered_markers() -> None:
    assert_markers(
        "The gate opens in the valley near the library.", ["gate", "valley", "library"]
    )


def test_two_chunk_reference() -> None:
    text = elevenlabs_passage()
    assert 5000 < len(text) < 5700
    assert_markers(text, ["gate", "captain", "mountain", "library"])
    assert_seam(text)


@pytest.mark.parametrize("text", ["", SEAM.replace("window", "door"), SEAM + " "])
def test_seam_oracle(text: str) -> None:
    if text.strip() == SEAM:
        assert_seam(text)
    else:
        with pytest.raises(AssertionError, match="chunk seam"):
            assert_seam(text)


@pytest.mark.parametrize(
    "text", ["", "gate library", "library valley gate", "gate valley valley library"]
)
def test_bad_markers(text: str) -> None:
    with pytest.raises(AssertionError):
        assert_markers(text, ["gate", "valley", "library"])


def test_missing_prerequisite_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VOX_PLAYBACK_REQUIRE", raising=False)
    with pytest.raises(pytest.skip.Exception, match="missing"):
        prerequisite("missing")


def test_required_prerequisite_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOX_PLAYBACK_REQUIRE", "1")
    with pytest.raises(pytest.fail.Exception, match="missing"):
        prerequisite("missing")
