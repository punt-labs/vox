"""Render parsed amounts as currency units."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import final

from punt_vox._currency_grammar import SCALES, UNITS
from punt_vox._currency_patterns import CurrencyPatterns

SPOKEN_SIGNS = {"-": "minus ", "\u2212": "minus ", "+": "plus ", "": ""}
_MINOR_UNITS = {"$": ("cent", "cents"), "€": ("cent", "cents"), "£": ("penny", "pence")}


@final
class CurrencyAmount:
    """Render one amount with its scale, minor units, or unsigned range end."""

    @classmethod
    def speak(cls, match: re.Match[str], prefix: str = "") -> str:
        """Speak the main or endpoint amount in the matched expression."""
        symbol, amount, sign = CurrencyPatterns.prose_fields(match, prefix)
        word = match[prefix + "word"] or ""
        scale = SCALES.get(word.lower(), "")
        suffix = "" if scale else word
        span = cls._speak_bare_end(match)
        if scale or span:
            parts = (cls._spoken_number(amount), scale, span, UNITS[symbol][1])
            money = " ".join(filter(None, parts))
        else:
            money = cls._speak_unscaled(symbol, amount)
        return " ".join(filter(None, (SPOKEN_SIGNS[sign] + money, suffix)))

    @classmethod
    def _speak_bare_end(cls, match: re.Match[str]) -> str:
        if not (end := CurrencyPatterns.bare_end(match)):
            return ""
        end_scale = SCALES.get((match["bare_scale"] or match["word"] or "").lower(), "")
        return " ".join(filter(None, ("to", cls._spoken_number(end), end_scale)))

    @classmethod
    def _speak_unscaled(cls, symbol: str, amount: str) -> str:
        whole, _, fraction = amount.partition(".")
        if symbol in _MINOR_UNITS and 1 <= len(fraction) <= 2:
            return cls._speak_decimal(symbol, whole, fraction)
        is_one = not fraction and Decimal(amount.replace(",", "")) == 1
        return f"{cls._spoken_number(amount)} {UNITS[symbol][not is_one]}"

    @staticmethod
    def _speak_decimal(symbol: str, whole: str, fraction: str) -> str:
        major = Decimal(whole.replace(",", "") or "0")
        unit = UNITS[symbol][major != 1]
        minor = int(fraction.ljust(2, "0"))
        if not minor:
            return f"{whole or '0'} {unit}"
        minor_unit = _MINOR_UNITS[symbol][minor != 1]
        if not major:
            qualifier = "euro " if symbol == "€" else ""
            return f"{minor} {qualifier}{minor_unit}"
        return f"{whole} {unit} and {minor} {minor_unit}"

    @staticmethod
    def _spoken_number(amount: str) -> str:
        return "0" + amount if amount.startswith(".") else amount
