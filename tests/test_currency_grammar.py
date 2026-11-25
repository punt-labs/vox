"""Match attached and spaced scale words in the prose currency grammar."""

import pytest

from punt_vox._currency_grammar import CurrencyGrammar


@pytest.mark.parametrize(
    ("text", "scale", "end_scale"),
    [
        ("$1million", "million", None),
        ("$1 million", " million", None),
        ("£2\N{NO-BREAK SPACE}billion", "\N{NO-BREAK SPACE}billion", None),
        ("$1 million-$2 billion", " million", " billion"),
    ],
)
def test_spaced_prose_pattern(text: str, scale: str, end_scale: str | None) -> None:
    match = CurrencyGrammar.spaced_prose_pattern(text).fullmatch(text)
    assert match is not None
    assert match.group("word", "end_word") == (scale, end_scale)


@pytest.mark.parametrize("text", ["$1 m", "$1\nmillion", "$1 millionth"])
def test_spaced_prose_pattern_rejects_non_scale_words(text: str) -> None:
    assert CurrencyGrammar.spaced_prose_pattern(text).fullmatch(text) is None
