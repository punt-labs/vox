"""Tests for speaking a parsed currency amount and its units."""

from __future__ import annotations

import pytest

from punt_vox._currency_amount import CurrencyAmount
from punt_vox._currency_grammar import CurrencyGrammar


@pytest.mark.parametrize(
    ("text", "prefix", "expected"),
    [
        ("$1", "", "1 dollar"),
        ("$1.25", "", "1 dollar and 25 cents"),
        ("£.01", "", "1 penny"),
        ("€1,50", "", "1 euro and 50 cents"),
        ("¥2million", "", "2 million yen"),
        ("-€1m\u2013€2b", "end_", "2 billion euros"),
    ],
)
def test_speak_amount(text: str, prefix: str, expected: str) -> None:
    match = CurrencyGrammar.prose_pattern(text).fullmatch(text)
    assert match is not None
    assert CurrencyAmount.speak(match, prefix) == expected
