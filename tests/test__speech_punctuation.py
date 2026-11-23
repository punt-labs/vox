"""Tests for spoken token punctuation and amount expansion."""

from __future__ import annotations

import pytest

from punt_vox._speech_punctuation import SpeechPunctuation


@pytest.mark.parametrize(
    ("token", "core", "suffix"),
    [
        ("[\u2212£.50],", "minus 0.50 pounds", ","),
        ("+€1,200!", "plus 1,200 euros", "!"),
        ("$1", "1 dollar", ""),
        ("$1.00", "1.00 dollars", ""),
        ("$HOME", "HOME", ""),
        ("€", "", ""),
    ],
)
def test_split_currency_token(token: str, core: str, suffix: str) -> None:
    assert SpeechPunctuation.split(token) == (core, suffix)


def test_combining_mark_after_trailing_underscore() -> None:
    assert SpeechPunctuation.split("foo_\u0301") == ("foo", "")
