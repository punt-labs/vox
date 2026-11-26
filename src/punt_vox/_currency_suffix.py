"""Recognize trailing signs and move them before prose amounts."""

from __future__ import annotations

import re
from typing import final

from punt_vox._currency_grammar import UNITS, CurrencyGrammar
from punt_vox._currency_patterns import CurrencyPatterns

_SYMBOLS = "".join(UNITS)
_TRAILING_PUNCTUATION = rf"(?:[^\w\s`{_SYMBOLS}]|_)*"
_DASH = r"[-\u2013]"
_PREFIX_RANGE = CurrencyGrammar.prose_pattern("")


@final
class SuffixCurrency:
    """Canonicalize trailing signs using the prefix range grammar as authority."""

    @staticmethod
    def amount_pattern(symbols: str) -> str:
        """Match suffix amounts and candidate ranges before a trailing sign."""
        decimal, number = CurrencyPatterns.EURO_DECIMAL, CurrencyPatterns.NUMBER
        sign, dotted = CurrencyPatterns.SIGN, CurrencyPatterns.DOTTED
        return (
            rf"(?P<leading>_*[^\w\s~/.,`{symbols}]*?)"
            rf"(?P<sign>{sign}?)(?P<amount>{decimal}(?=€|{_DASH})|{number})"
            rf"(?P<range_end>{_DASH}(?:{decimal}|{dotted}|{number}))?"
            rf"(?P<symbol>[{symbols}])"
        )

    @classmethod
    def move_signs(cls, prose: str, tag_pattern: str) -> str:
        """Move suffix signs at prose and tag boundaries."""
        boundary = rf"(?P<boundary>(?<!\S)|{tag_pattern})"
        return re.sub(
            rf"{boundary}{cls.amount_pattern(_SYMBOLS)}"
            rf"(?:(?P<before_tag>{_TRAILING_PUNCTUATION})"
            rf"(?=(?:{tag_pattern})+(?:\w|[.,]\d))"
            rf"|(?={_TRAILING_PUNCTUATION}(?:(?!\S)|{tag_pattern})))",
            cls._prefix_amount,
            prose,
        )

    @staticmethod
    def _prefix_amount(match: re.Match[str]) -> str:
        amount = match.expand(r"\g<sign>\g<symbol>\g<amount>\g<range_end>")
        if match["range_end"] and not _PREFIX_RANGE.fullmatch(amount):
            return match[0]
        leading = match.expand(r"\g<boundary>\g<leading>")
        before_tag = match["before_tag"]
        trailing = "" if before_tag is None else f"{before_tag} "
        return f"{leading}{amount}{trailing}"
