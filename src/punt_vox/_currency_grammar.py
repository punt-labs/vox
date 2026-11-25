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
_WRITTEN_SCALES = "|".join(scale for scale in SCALES if len(scale) > 1)
_SYMBOLS = "".join(UNITS)
_PROSE_AMOUNT = CurrencyPatterns.prose_amount(_SYMBOLS)
_RANGE_END_AMOUNT = CurrencyPatterns.prose_amount(_SYMBOLS, "end_")
_LETTER = r"[^\W\d_]"


@final
class CurrencyGrammar:
    """Recognize attached money using symbol-specific numeric rules."""

    CODE_AMOUNT = re.compile(CurrencyPatterns.code_amount(_SYMBOLS))
    HORIZONTAL_GAP = re.compile(r"[^\S\r\n\v\f\x1c-\x1e\x85\u2028\u2029]+")

    @classmethod
    def spaced_prose_pattern(cls, text: str) -> re.Pattern[str]:
        """Allow horizontal space before a written scale, including in ranges."""
        return cls.prose_pattern(text, scale_gap=cls.HORIZONTAL_GAP.pattern)

    @classmethod
    def prose_pattern(cls, text: str, *, scale_gap: str = "") -> re.Pattern[str]:
        """Build the prose grammar with any combining marks present in *text*."""
        word = cls._word_pattern(text)
        spaced_scale = rf"{scale_gap}(?i:{_WRITTEN_SCALES})"
        amount_word = rf"{word}|{spaced_scale}"
        return re.compile(
            rf"{_PROSE_AMOUNT}(?P<word>{amount_word})?"
            rf"(?:[-\u2013]{_RANGE_END_AMOUNT}(?P<end_word>{amount_word})?"
            rf"|{CurrencyPatterns.BARE_RANGE}"
            rf"(?P<bare_scale>(?i:{_SCALE_PATTERN})|{spaced_scale})?)?"
            rf"(?P<rates>(?:/{word})*)"
            rf"(?(bare_end)(?![{_SYMBOLS}]))"
        )

    @classmethod
    def _word_pattern(cls, text: str) -> str:
        """Match letters and any combining marks found in the input."""
        if not text.isascii() and (marks := cls._combining_marks(text)):
            return rf"{_LETTER}(?:{_LETTER}|[{marks}])*"
        return rf"{_LETTER}+"

    @staticmethod
    def _combining_marks(text: str) -> str:
        marks = {char for char in text if unicodedata.category(char).startswith("M")}
        return re.escape("".join(sorted(marks)))
