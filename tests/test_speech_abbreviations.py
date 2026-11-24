"""Tests for whole-word speech abbreviation lookups."""

import pytest

from punt_vox._speech_abbreviations import SpeechAbbreviations


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        ("STDERR", "standard error"),
        ("fWiW", "for what it's worth"),
        ("stderr_log", None),
        ("unknown", None),
    ],
)
def test_expand_matches_whole_words_case_insensitively(
    word: str, expected: str | None
) -> None:
    assert SpeechAbbreviations.expand(word) == expected
