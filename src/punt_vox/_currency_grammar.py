"""The numeric and token grammars shared by speech currency formats."""

from __future__ import annotations

import re
import unicodedata
from typing import final

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
_SIGN = r"[-+\u2212]"
_NUMBER = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\.\d+"
_PREFIX_AMOUNT = (
    rf"(?P<{{p}}sign_before>{_SIGN})?"
    rf"(?P<{{p}}symbol>[{_SYMBOLS}])"
    rf"(?({{p}}sign_before)|(?P<{{p}}sign_after>{_SIGN})?)"
    rf"(?P<{{p}}amount>{_NUMBER})"
)
_SUFFIX_AMOUNT = (
    rf"(?P<leading>_*[^\w\s~/.,`{_SYMBOLS}]*?)"
    rf"(?P<sign>{_SIGN}?)(?P<amount>{_NUMBER})(?P<symbol>[{_SYMBOLS}])"
    rf"(?=(?:[^\w\s`{_SYMBOLS}]|_)*(?!\S))"
)
_LETTER = r"[^\W\d_]"


@final
class CurrencyGrammar:
    """Recognize attached money without guessing a numeric locale."""

    code_amount = re.compile(_PREFIX_AMOUNT.replace("{p}", ""))

    @classmethod
    def prose_pattern(cls, text: str) -> re.Pattern[str]:
        """Build the prose grammar with any combining marks present in *text*."""
        word = cls._word_pattern(text)
        return re.compile(
            rf"{cls.code_amount.pattern}(?P<word>{word})?"
            rf"(?:[-\u2013]{_PREFIX_AMOUNT.replace('{p}', 'end_')}(?P<end_word>{word})?"
            rf"|\u2013(?P<bare_end>{_NUMBER})(?P<bare_scale>(?i:{_SCALE_PATTERN}))?)?"
            rf"(?P<rates>(?:/{word})*)"
        )

    @staticmethod
    def prefix_suffix_amounts(
        prose: str, *, after: re.Pattern[str] | None = None
    ) -> str:
        """Put prose amounts in prefix order for the shared money renderer."""
        boundary = rf"(?<!\S)|{after.pattern}" if after is not None else r"(?<!\S)"
        return re.sub(
            rf"(?P<boundary>{boundary}){_SUFFIX_AMOUNT}",
            r"\g<boundary>\g<leading>\g<sign>\g<symbol>\g<amount>",
            prose,
        )

    @staticmethod
    def _word_pattern(text: str) -> str:
        if text.isascii():
            return rf"{_LETTER}+"
        marks = {char for char in text if unicodedata.category(char).startswith("M")}
        if marks:
            return rf"{_LETTER}(?:{_LETTER}|[{re.escape(''.join(sorted(marks)))}])*"
        return rf"{_LETTER}+"
