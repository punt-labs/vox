"""Tests for RecordingName, the id a new recording is stored under."""

from __future__ import annotations

import pytest

from punt_vox.recording_name import RecordingName
from punt_vox.types import generate_filename


@pytest.mark.parametrize("name", ["", "  ", "\t", " \n ", "\u3000"])
def test_blank_name_rejected(name: str) -> None:
    with pytest.raises(ValueError, match="must not be empty or whitespace"):
        RecordingName(name)


def test_bare_name_rules_still_apply() -> None:
    with pytest.raises(ValueError, match="recording name must not contain a path"):
        RecordingName("a/b.mp3")


def test_explicit_name_kept_verbatim() -> None:
    assert RecordingName(" take 1.mp3").value == " take 1.mp3"


def test_absent_name_content_addresses_text() -> None:
    assert RecordingName.for_take(None, "hello").value == generate_filename("hello")


def test_explicit_name_wins_over_text() -> None:
    assert RecordingName.for_take("greeting.mp3", "hello").value == "greeting.mp3"
