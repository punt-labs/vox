"""Render monetary expressions and literal currency signs for speech."""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal
from typing import final

_UNITS = {
    "$": ("dollar", "dollars"),
    "€": ("euro", "euros"),
    "£": ("pound", "pounds"),
    "¥": ("yen", "yen"),
    "₹": ("rupee", "rupees"),
    "₩": ("won", "won"),
    "₽": ("ruble", "rubles"),
}
_SIGNS = {"-": "minus ", "\u2212": "minus ", "+": "plus ", "": ""}
_SCALES = {
    "k": "thousand",
    "m": "million",
    "b": "billion",
    "thousand": "thousand",
    "million": "million",
    "billion": "billion",
    "trillion": "trillion",
}
_AMOUNT = (
    r"(?P<{p}sign_before>[-+\u2212])?"
    rf"(?P<{{p}}symbol>[{''.join(_UNITS)}])"
    r"(?({p}sign_before)|(?P<{p}sign_after>[-+\u2212])?)"
    r"(?P<{p}amount>(?:\d{{1,3}}(?:,\d{{3}})+|\d+)(?:\.\d+)?|\.\d+)"
)
_CODE_AMOUNT = re.compile(_AMOUNT.format(p=""))
_LETTER = r"[^\W\d_]"


@final
class CurrencySpeech:
    """Use the same amount grammar in prose and inline code."""

    @staticmethod
    def prose_pattern(text: str) -> re.Pattern[str]:
        """Build the prose grammar with any combining marks present in *text*."""
        marks = (
            {char for char in text if unicodedata.category(char).startswith("M")}
            if not text.isascii()
            else set()
        )
        word = (
            rf"{_LETTER}(?:{_LETTER}|[{re.escape(''.join(sorted(marks)))}])*"
            if marks
            else rf"{_LETTER}+"
        )
        return re.compile(
            rf"{_CODE_AMOUNT.pattern}(?P<word>{word})?"
            rf"(?:[-\u2013]{_AMOUNT.format(p='end_')}(?P<end_word>{word})?)?"
            rf"(?:/(?P<rate>{word}))?"
        )

    @classmethod
    def speak_prose(cls, core: str) -> str:
        """Render an amount with an optional range, rate, scale, or attached word."""
        match = cls.prose_pattern(core).fullmatch(core.lstrip("_"))
        if match is None:
            return core
        spoken = cls._speak_amount(match)
        if match["end_symbol"]:
            spoken += f" to {cls._speak_amount(match, 'end_')}"
        if rate := match["rate"]:
            spoken += f" per {rate}"
        return spoken

    @classmethod
    def speak_code(cls, token: str) -> str:
        """Spell code currency signs literally within a token."""
        return _CODE_AMOUNT.sub(cls._speak_code_sign, token)

    @staticmethod
    def _speak_amount(match: re.Match[str], prefix: str = "") -> str:
        symbol, amount = match[prefix + "symbol"], match[prefix + "amount"]
        sign = match[prefix + "sign_before"] or match[prefix + "sign_after"] or ""
        word = match[prefix + "word"] or ""
        scale = _SCALES.get(word.lower(), "")
        singular, plural = _UNITS[symbol]
        is_one = (
            not scale and "." not in amount and Decimal(amount.replace(",", "")) == 1
        )
        unit = singular if is_one else plural
        number = "0" + amount if amount.startswith(".") else amount
        return " ".join(
            filter(None, (_SIGNS[sign] + number, scale, unit, "" if scale else word))
        )

    @staticmethod
    def _speak_code_sign(match: re.Match[str]) -> str:
        symbol, amount = match.group("symbol", "amount")
        before, after = match.group("sign_before", "sign_after")
        unit, _ = _UNITS[symbol]
        left = " " if match.string[match.start() - 1 : match.start()].isalnum() else ""
        right = " " if match.string[match.end() : match.end() + 1].isalnum() else ""
        return (
            f"{left}{_SIGNS[before or '']}{unit} sign "
            f"{_SIGNS[after or '']}{amount}{right}"
        )
