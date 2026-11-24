"""Suffix amount recognition before money rendering."""

import pytest

from punt_vox._currency_grammar import CurrencyGrammar


@pytest.mark.parametrize(
    ("prose", "expected"),
    [
        ("(+5¥), \u2212.5£.", "(+¥5), \u2212£.5."),
        ("₹5 5₹ 5₩ 5₽", "₹5 ₹5 ₩5 ₽5"),
        ("€1,50 1,50€ ,50€ /tmp/5€", "€1,50 1,50€ ,50€ /tmp/5€"),
        ("5k€ 5€/month 5€-10€", "5k€ 5€/month 5€-10€"),
    ],
)
def test_prefix_suffix_amounts(prose: str, expected: str) -> None:
    assert CurrencyGrammar.prefix_suffix_amounts(prose) == expected
