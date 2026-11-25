"""Prose amount canonicalization before money rendering."""

import pytest

from punt_vox._currency_prose import ProseCurrency
from punt_vox._link_labels import VIBE_TAG_RE


@pytest.mark.parametrize(
    ("prose", "expected"),
    [
        ("(+5¥), \u2212.5£.", "(+¥5), \u2212£.5."),
        ("₹5 5₹ 5₩ 5₽", "₹5 ₹5 ₩5 ₽5"),
        ("€1,50 1,50€ ,50€ /tmp/5€", "€1,50 €1,50 ,50€ /tmp/5€"),
        ("5k€ 5€/month 5€-10€", "5k€ 5€/month 5€-10€"),
    ],
)
def test_prefix_suffix_amounts(prose: str, expected: str) -> None:
    assert ProseCurrency.canonicalize(prose, tag=None) == expected


@pytest.mark.parametrize(
    ("spaced", "attached"),
    [
        ("$1 million-[pause]$2 million", "$1million-[pause]$2million"),
        ("$1 million[pause]€2 million", "$1million[pause]€2million"),
        ("$1 million—€2 billion", "$1million—€2billion"),
        ("$1 million—enough", "$1million—enough"),
    ],
)
def test_scale_join_only_removes_whitespace(spaced: str, attached: str) -> None:
    assert ProseCurrency.canonicalize(spaced, tag=VIBE_TAG_RE) == attached
