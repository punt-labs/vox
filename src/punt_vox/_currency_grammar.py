"""The numeric and token grammars shared by speech currency formats."""

from __future__ import annotations

import re
import unicodedata
from typing import final

from punt_vox._currency_patterns import CurrencyPatterns

UNITS = {
    "$": ("dollar", "dollars"),
    "€": ("euro", "euros"),
    "£": ("pound", "pounds"),
    "¥": ("yen", "yen"),
    "₹": ("rupee", "rupees"),
    "₩": ("won", "won"),
    "₽": ("ruble", "rubles"),
}
SCALES = {
    "k": "thousand",
    "m": "million",
    "b": "billion",
    "thousand": "thousand",
    "million": "million",
    "billion": "billion",
    "trillion": "trillion",
}
_SCALE_PATTERN = "|".join(SCALES)
_SYMBOLS = "".join(UNITS)
_PROSE_AMOUNT = CurrencyPatterns.prose_amount(_SYMBOLS)
_RANGE_END_AMOUNT = CurrencyPatterns.prose_amount(_SYMBOLS, "end_")
_SUFFIX_AMOUNT = CurrencyPatterns.suffix_amount(_SYMBOLS)
_TRAILING_PUNCTUATION = rf"(?:[^\w\s`{_SYMBOLS}]|_)*"
_LETTER = r"[^\W\d_]"


@final
class CurrencyGrammar:
    """Recognize attached money using symbol-specific numeric rules."""

    CODE_AMOUNT = re.compile(CurrencyPatterns.code_amount(_SYMBOLS))

    @staticmethod
    def prose_pattern(text: str) -> re.Pattern[str]:
        """Build the prose grammar with any combining marks present in *text*."""
        word = CurrencyGrammar._word_pattern(text)
        return re.compile(
            rf"{_PROSE_AMOUNT}(?P<word>{word})?"
            rf"(?:[-\u2013]{_RANGE_END_AMOUNT}(?P<end_word>{word})?"
            rf"|\u2013(?P<bare_end>{CurrencyPatterns.NUMBER})(?P<bare_scale>(?i:{_SCALE_PATTERN}))?)?"
            rf"(?P<rates>(?:/{word})*)"
        )

    @classmethod
    def prefix_suffix_amounts(
        cls,
        prose: str,
        *,
        tag: re.Pattern[str] | None = None,  # No tags in plain prose.
    ) -> str:
        """Put prose amounts in prefix order for the shared money renderer.

        When supplied, ``tag`` matches expressive tags that delimit amounts.
        With ``None``, only ordinary prose boundaries apply.
        """
        tag_pattern = tag.pattern if tag is not None else r"(?!)"
        return re.sub(
            rf"(?P<boundary>(?<!\S)|{tag_pattern}){_SUFFIX_AMOUNT}"
            rf"(?:(?P<before_tag>{_TRAILING_PUNCTUATION})"
            rf"(?=(?:{tag_pattern})+(?:\w|[.,]\d))"
            rf"|(?={_TRAILING_PUNCTUATION}(?:(?!\S)|{tag_pattern})))",
            cls._prefix_amount,
            prose,
        )

    @staticmethod
    def _prefix_amount(match: re.Match[str]) -> str:
        amount = match.expand(r"\g<boundary>\g<leading>\g<sign>\g<symbol>\g<amount>")
        before_tag = match["before_tag"]
        return amount if before_tag is None else f"{amount}{before_tag} "

    @staticmethod
    def _word_pattern(text: str) -> str:
        if text.isascii():
            return rf"{_LETTER}+"
        marks = {char for char in text if unicodedata.category(char).startswith("M")}
        if marks:
            return rf"{_LETTER}(?:{_LETTER}|[{re.escape(''.join(sorted(marks)))}])*"
        return rf"{_LETTER}+"
