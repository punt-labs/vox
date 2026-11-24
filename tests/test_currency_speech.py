"""Tests for monetary expressions and literal currency signs."""

from __future__ import annotations

import pytest

from punt_vox._currency_speech import CurrencySpeech


@pytest.mark.parametrize(
    ("core", "expected"),
    [
        ("£.50\u20131.25/user/month", "0.50 to 1.25 pounds per user per month"),
        ("-₹5/घंटा", "minus 5 rupees per घंटा"),
        ("$1million", "1 million dollars"),
        ("€1\u2013£2", "1 euro to 2 pounds"),
        ("$5/2", "$5/2"),
    ],
)
def test_speak_prose(core: str, expected: str) -> None:
    assert CurrencySpeech.speak_prose(core) == expected


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("x$1million", "x dollar sign 1 million"),
        ("-$5", "minus dollar sign 5"),
        ("$-5", "dollar sign minus 5"),
        ("$HOME", "$HOME"),
    ],
)
def test_speak_code(token: str, expected: str) -> None:
    assert CurrencySpeech.speak_code(token) == expected
