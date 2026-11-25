"""Preserve standalone comparisons before normalizing speech words."""

from __future__ import annotations

import re
from typing import final

from punt_vox._currency_grammar import CurrencyGrammar
from punt_vox._inline_code import InlineCode
from punt_vox._link_labels import VIBE_TAG_RE

_SPOKEN = {
    "<": "less than",
    ">": "greater than",
    "<=": "less than or equal to",
    ">=": "greater than or equal to",
    "==": "equal to",
    "!=": "not equal to",
}
_HORIZONTAL_SPACE = r"[^\S\r\n]+"
_TAG_PREFIX = rf"(?:{VIBE_TAG_RE.pattern}{_HORIZONTAL_SPACE})*"
_QUOTE_PREFIX = r"(?:(?:[-+*]|\d{1,9}[.)])[ \t]+|>[ \t]*)*>"
_OPERAND = r"[^\s<>!=]+"
_WRAPPERS = """'"`()[]{}*.,;:?!"""
_NUMBER_RE = re.compile(r"[-+\u2212]?(?:\d+(?:[.,]\d+)*|[.,]\d+)(?:[eE][+-]?\d+)?%?")
_RADIX_RE = re.compile(r"0(?:[xX][\da-fA-F]+|[bB][01]+|[oO][0-7]+)")


@final
class SpeechInput:
    """Prepare speech input without treating Markdown separators as operators."""

    @classmethod
    def prepare(cls, text: str, *, vibed: bool = False) -> str:
        """Rewrite Markdown labels, amounts, and same-line comparison operators."""
        pattern = cls._VIBED_COMPARISON_RE if vibed else cls._COMPARISON_RE
        return InlineCode.rewrite_currency(pattern.sub(cls._spell, text), vibed=vibed)

    @staticmethod
    def _pattern(tag_prefix: str) -> re.Pattern[str]:
        gap = _HORIZONTAL_SPACE + tag_prefix
        return re.compile(
            rf"(?m)(?:^|(?<=\r))[ \t]*{tag_prefix}{_QUOTE_PREFIX}|"
            rf"(?<!\S)(?P<left>{_OPERAND})(?P<gap>{gap})(?P<op>[<>]=?|[!=]=)"
            rf"(?={gap}(?P<right>{_OPERAND})(?!\S))"
        )

    @staticmethod
    def _spell(match: re.Match[str]) -> str:
        operator = match["op"]
        if operator is None:
            return match[0]
        left, right = match["left"], match["right"]
        if not all(SpeechInput._is_operand(value) for value in (left, right)):
            return match[0]
        if operator in ("<", ">") and not SpeechInput._ordered_operands(left, right):
            return match[0]
        return f"{left}{match['gap']}{_SPOKEN[operator]}"

    @staticmethod
    def _ordered_operands(left: str, right: str) -> bool:
        # Bare angle marks can also separate breadcrumbs or redirect a shell command.
        operands = (left.strip(_WRAPPERS), right.strip(_WRAPPERS))
        return all(
            (len(value) == 1 and value.isalpha())
            or _NUMBER_RE.fullmatch(value)
            or _RADIX_RE.fullmatch(value)
            or CurrencyGrammar.CODE_AMOUNT.fullmatch(value)
            for value in operands
        )

    @staticmethod
    def _is_operand(value: str) -> bool:
        return VIBE_TAG_RE.fullmatch(value) is None and any(
            char.isalnum() for char in value
        )

    _COMPARISON_RE = _pattern("")
    _VIBED_COMPARISON_RE = _pattern(_TAG_PREFIX)
