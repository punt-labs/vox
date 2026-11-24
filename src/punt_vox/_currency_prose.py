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
        boundary = rf"(?P<boundary>(?<!\S)|{tag_pattern})"
        return re.sub(
            rf"{boundary}{_SUFFIX_AMOUNT}"
            rf"(?:(?P<before_tag>{_TRAILING_PUNCTUATION})"
            rf"(?=(?:{tag_pattern})+(?:\w|[.,]\d))"
            rf"|(?={_TRAILING_PUNCTUATION}(?:(?!\S)|{tag_pattern})))",
            cls._prefix_amount,
            cls._attach_scale(prose, tag_pattern),
        )

    @staticmethod
    def _attach_scale(prose: str, tag_pattern: str) -> str:
        boundary = rf"(?P<boundary>(?<![^\s\u2014])|{tag_pattern})"
        leading = rf"[^\w\s~/.,`{_SYMBOLS}]*"
        currency = CurrencyGrammar.spaced_prose_pattern(prose).pattern
        end = r"(?![^\W_]|-[^\W\d_])"
        return re.sub(
            rf"{boundary}(?P<price>_*{leading}{currency}){end}",
            lambda match: (
                match["boundary"]
                + CurrencyGrammar.HORIZONTAL_GAP.sub("", match["price"])
            ),
            prose,
        )

    @staticmethod
    def _prefix_amount(match: re.Match[str]) -> str:
        amount = match.expand(r"\g<boundary>\g<leading>\g<sign>\g<symbol>\g<amount>")
        before_tag = match["before_tag"]
        return amount if before_tag is None else f"{amount}{before_tag} "
