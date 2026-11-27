"""Canonicalize prose amounts before whitespace tokenization."""

from __future__ import annotations

import re
from typing import final

from punt_vox._currency_grammar import UNITS, CurrencyGrammar
from punt_vox._currency_suffix import SuffixCurrency

_SYMBOLS = "".join(UNITS)
_CURRENCY_SYMBOL = re.compile(rf"[{_SYMBOLS}]")


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
        return SuffixCurrency.move_signs(
            cls._attach_scale(prose, tag_pattern), tag_pattern
        )

    @classmethod
    def _attach_scale(cls, prose: str, tag_pattern: str) -> str:
        boundary = rf"(?P<boundary>(?<![^\s\u2014])|{tag_pattern})"
        leading = rf"[^\w\s~/.,`{_SYMBOLS}]*"
        currency = CurrencyGrammar.spaced_prose_pattern(prose).pattern
        end = r"(?![^\W_]|-[^\W\d_])"
        return re.sub(
            rf"{boundary}(?P<price>_*{leading}{currency}){end}",
            cls._remove_scale_gap,
            prose,
        )

    @staticmethod
    def _remove_scale_gap(match: re.Match[str]) -> str:
        return match["boundary"] + CurrencyGrammar.HORIZONTAL_GAP.sub(
            "", match["price"]
        )
