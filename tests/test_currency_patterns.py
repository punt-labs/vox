"""Currency number patterns and their canonical fields."""

import re

import pytest

from punt_vox._currency_patterns import CurrencyPatterns


@pytest.mark.parametrize("prefix", ["", "end_"])
def test_prose_fields_preserve_grouping_and_canonicalize_fraction(prefix: str) -> None:
    match = re.fullmatch(CurrencyPatterns.prose_amount("$€", prefix), "€-1,500,25")
    assert match is not None
    assert CurrencyPatterns.prose_fields(match, prefix) == ("€", "1,500.25", "-")


def test_code_does_not_consume_comma_decimal() -> None:
    match = re.match(CurrencyPatterns.code_amount("$€"), "€1,50")
    assert match is not None
    assert match["amount"] == "1"


@pytest.mark.parametrize(("text", "eligible"), [("1,50€", True), ("1,50$", False)])
def test_suffix_decimal_requires_euro(text: str, eligible: bool) -> None:
    assert (re.fullmatch(CurrencyPatterns.suffix_amount("$€"), text) is not None) == (
        eligible
    )
