"""Render monetary expressions and literal currency signs for speech."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import final

from punt_vox._currency_grammar import SCALES, UNITS, CurrencyGrammar

_SIGNS = {"-": "minus ", "\u2212": "minus ", "+": "plus ", "": ""}


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
        return CurrencyGrammar.code_amount.sub(cls._speak_code_sign, token)

    @classmethod
    def _speak_amount(cls, match: re.Match[str], prefix: str = "") -> str:
        symbol, amount = match[prefix + "symbol"], match[prefix + "amount"]
        sign = match[prefix + "sign_before"] or match[prefix + "sign_after"] or ""
        word = match[prefix + "word"] or ""
        scale = SCALES.get(word.lower(), "")
        suffix = "" if scale else word
        singular, plural = UNITS[symbol]
        span = "" if prefix else cls._speak_bare_end(match, scale)
        unit = singular if not (scale or span) and cls._is_one(amount) else plural
        number = _SIGNS[sign] + cls._spoken_number(amount)
        return " ".join(filter(None, (number, scale, span, unit, suffix)))

    @classmethod
    def _speak_bare_end(cls, match: re.Match[str], start_scale: str) -> str:
        if not (end := match["bare_end"]):
            return ""
        end_scale = SCALES.get((match["bare_scale"] or "").lower(), start_scale)
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
