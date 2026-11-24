"""Tests for spoken token punctuation and amount expansion."""

from __future__ import annotations

import pytest

from punt_vox._speech_punctuation import SpeechPunctuation


@pytest.mark.parametrize(
    ("token", "core", "suffix"),
    [
        ("[\u2212£.50],", "minus 50 pence", ","),
        ("+€1,200!", "plus 1,200 euros", "!"),
        ("$1", "1 dollar", ""),
        ("$1.00", "1 dollar", ""),
        ("$HOME", "HOME", ""),
        ("€", "", ""),
    ],
)
def test_split_currency_token(token: str, core: str, suffix: str) -> None:
    assert SpeechPunctuation.split(token) == (core, suffix)


def test_combining_mark_after_trailing_underscore() -> None:
    assert SpeechPunctuation.split("foo_\u0301") == ("foo", "")


@pytest.mark.parametrize(
    ("token", "core", "suffix"),
    [
        ("C++", "C plus plus", ""),
        ("C#", "C sharp", ""),
        ("F#.", "F sharp", "."),
        ("(C++),", "C plus plus", ","),
        ("`C#`", "C sharp", ""),
        ("Notepad++", "Notepad plus plus", ""),
        ("i++;", "i plus plus", ";"),
        ("_C#_", "_C sharp", ""),
        ("__C++__.", "__C plus plus", "."),
        ("_count++", "_count plus plus", ""),
    ],
)
def test_split_keeps_language_name_marks(token: str, core: str, suffix: str) -> None:
    assert SpeechPunctuation.split(token) == (core, suffix)


@pytest.mark.parametrize(
    ("token", "core"),
    [("#include", "include"), ("SHA#", "SHA"), ("C++17", "C++17")],
)
def test_split_leaves_other_marks_alone(token: str, core: str) -> None:
    assert SpeechPunctuation.split(token) == (core, "")
