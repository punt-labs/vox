"""Render monetary expressions and literal currency signs for speech."""

from __future__ import annotations

import re
from typing import final

from punt_vox._currency_amount import SPOKEN_SIGNS, CurrencyAmount
from punt_vox._currency_grammar import UNITS, CurrencyGrammar


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
        pattern = cls.prose_pattern(core)
        first, *rest = core.split("—")
        if not (match := pattern.fullmatch(first.lstrip("_"))):
            return core
        parts = [cls._render(match)]
        for part in rest:
            next_match = pattern.fullmatch(part.lstrip("_"))
            parts.append(cls._render(next_match) if next_match else part)
        return "—".join(parts)

    @staticmethod
    def _render(match: re.Match[str]) -> str:
        spoken = CurrencyAmount.speak(match)
        if match["end_symbol"]:
            spoken += f" to {CurrencyAmount.speak(match, 'end_')}"
        spoken += match["rates"].replace("/", " per ")
        return spoken if spoken.endswith("s") else spoken + (match["possessive"] or "")

    @classmethod
    def speak_code(cls, token: str) -> str:
        """Spell code currency signs literally within a token."""
        return CurrencyGrammar.CODE_AMOUNT.sub(cls._speak_code_sign, token)

    @staticmethod
    def _speak_code_sign(match: re.Match[str]) -> str:
        symbol, amount = match.group("symbol", "amount")
        before, after = match.group("sign_before", "sign_after")
        unit, _ = UNITS[symbol]
        left = " " if match.string[match.start() - 1 : match.start()].isalnum() else ""
        right = " " if match.string[match.end() : match.end() + 1].isalnum() else ""
        return (
            f"{left}{SPOKEN_SIGNS[before or '']}{unit} sign "
            f"{SPOKEN_SIGNS[after or '']}{amount}{right}"
        )
