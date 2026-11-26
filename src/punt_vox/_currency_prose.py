"""Canonicalize prose amounts before whitespace tokenization."""

from __future__ import annotations

import re
from typing import final

from punt_vox._currency_grammar import UNITS, CurrencyGrammar
from punt_vox._currency_patterns import CurrencyPatterns

_SYMBOLS = "".join(UNITS)
_CURRENCY_SYMBOL = re.compile(rf"[{_SYMBOLS}]")
_SUFFIX_AMOUNT = CurrencyPatterns.suffix_amount(_SYMBOLS)
_TRAILING_PUNCTUATION = rf"(?:[^\w\s`{_SYMBOLS}]|_)*"


@final
class ProseCurrency:
    """Prepare prose-only amount forms for the shared money renderer."""

    @classmethod
    def canonicalize(
        cls,
        prose: str,
        *,
        tag: re.Pattern[str] | None,  # Plain prose has no expressive tag boundaries.
    ) -> str:
        """Join written scales to prefix amounts and move trailing currency signs."""
        if not _CURRENCY_SYMBOL.search(prose):
            return prose
        tag_pattern = tag.pattern if tag is not None else r"(?!)"
        prefixed = re.sub(
            rf"(?P<boundary>(?<!\S)|{tag_pattern}){_SUFFIX_AMOUNT}"
            rf"(?:(?P<before_tag>{_TRAILING_PUNCTUATION})"
            rf"(?=(?:{tag_pattern})+(?:\w|[.,]\d))"
            rf"|(?={_TRAILING_PUNCTUATION}(?:(?!\S)|{tag_pattern})))",
            cls._prefix_amount,
            prose,
        )
        return cls._attach_scale(prefixed, tag_pattern)

    @classmethod
    def _attach_scale(cls, prose: str, tag_pattern: str) -> str:
        return re.sub(
            rf"(?:(?<![^\s\u2014])|{tag_pattern})_*[^\w\s~/.,`{_SYMBOLS}]*"
            rf"{CurrencyGrammar.spaced_prose_pattern(prose).pattern}(?![^\W_]|-[^\W\d_])"
            rf"(?:(?P<before_tag>[.,?!:;]*)"
            rf"(?=(?:{tag_pattern})+[-+\u2212]?[{_SYMBOLS}]))?",
            lambda match: (
                CurrencyGrammar.HORIZONTAL_GAP.sub("", match[0]) + cls._tag_gap(match)
            ),
            prose,
        )

    @classmethod
    def _prefix_amount(cls, match: re.Match[str]) -> str:
        amount = match.expand(r"\g<boundary>\g<leading>\g<sign>\g<symbol>\g<amount>")
        return amount + (match["before_tag"] or "") + cls._tag_gap(match)

    @staticmethod
    def _tag_gap(match: re.Match[str]) -> str:
        return "" if match["before_tag"] is None else " "
