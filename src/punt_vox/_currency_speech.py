"""Render monetary expressions and literal currency signs for speech."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import final

from punt_vox._currency_grammar import SCALES, UNITS, CurrencyGrammar
from punt_vox._currency_patterns import CurrencyPatterns

_SIGNS = {"-": "minus ", "\u2212": "minus ", "+": "plus ", "": ""}
_MINOR_UNITS = {"$": ("cent", "cents"), "€": ("cent", "cents"), "£": ("penny", "pence")}


@final
class CurrencySpeech:
    """Render parsed amounts as money in prose and literally in code."""

    @staticmethod
    def prose_pattern(text: str) -> re.Pattern[str]:
        """Recognize prose amounts before stripping punctuation."""
        return CurrencyGrammar.prose_pattern(text)

    @classmethod
    def speak_prose(cls, core: str) -> str:
        """Render an amount with an optional range, rate, scale, or attached word."""
        match = cls.prose_pattern(core).fullmatch(core.lstrip("_"))
        if match is None:
            return core
        spoken = cls._speak_amount(match)
        if match["end_symbol"]:
            spoken += f" to {cls._speak_amount(match, 'end_')}"
        return spoken + match["rates"].replace("/", " per ")

    @classmethod
    def speak_code(cls, token: str) -> str:
        """Spell code currency signs literally within a token."""
        return CurrencyGrammar.CODE_AMOUNT.sub(cls._speak_code_sign, token)

    @classmethod
    def _speak_amount(cls, match: re.Match[str], prefix: str = "") -> str:
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
        return " ".join(filter(None, (_SIGNS[sign] + money, suffix)))

    @classmethod
    def _speak_unscaled(cls, symbol: str, amount: str) -> str:
        units = UNITS[symbol]
        whole, _, fraction = amount.partition(".")
        if symbol not in _MINOR_UNITS or not 1 <= len(fraction) <= 2:
            return f"{cls._spoken_number(amount)} {units[not cls._is_one(amount)]}"
        major = Decimal(whole.replace(",", "") or "0")
        minor = int(fraction.ljust(2, "0"))
        if not minor:
            return f"{whole or '0'} {units[major != 1]}"
        minor_unit = _MINOR_UNITS[symbol][minor != 1]
        if not major:
            qualifier = "euro " if symbol == "€" else ""
            return f"{minor} {qualifier}{minor_unit}"
        return f"{whole} {units[major != 1]} and {minor} {minor_unit}"

    @classmethod
    def _speak_bare_end(cls, match: re.Match[str]) -> str:
        if not (end := match["bare_end"]):
            return ""
        end_scale = SCALES.get((match["bare_scale"] or match["word"] or "").lower(), "")
        return " ".join(filter(None, ("to", cls._spoken_number(end), end_scale)))

    @staticmethod
    def _is_one(amount: str) -> bool:
        return "." not in amount and Decimal(amount.replace(",", "")) == 1

    @staticmethod
    def _spoken_number(amount: str) -> str:
        return "0" + amount if amount.startswith(".") else amount

    @staticmethod
    def _speak_code_sign(match: re.Match[str]) -> str:
        symbol, amount = match.group("symbol", "amount")
        before, after = match.group("sign_before", "sign_after")
        unit, _ = UNITS[symbol]
        left = " " if match.string[match.start() - 1 : match.start()].isalnum() else ""
        right = " " if match.string[match.end() : match.end() + 1].isalnum() else ""
        return (
            f"{left}{_SIGNS[before or '']}{unit} sign "
            f"{_SIGNS[after or '']}{amount}{right}"
        )
