"""Preserve standalone comparisons before normalizing speech words."""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import final

from punt_vox._currency_grammar import CurrencyGrammar
from punt_vox._inline_code import InlineCode
from punt_vox._link_labels import VIBE_TAG_RE, LinkSpan
from punt_vox._speech_markdown import SpeechMarkdown

_SPOKEN = {
    "<": "less than",
    ">": "greater than",
    "<=": "less than or equal to",
    ">=": "greater than or equal to",
    "==": "equal to",
    "!=": "not equal to",
}
_BARE_ANGLES = frozenset("<>")
_WORD_RE = re.compile(r"\S+")
_QUOTE_PREFIX = r"(?:(?:[-+*]|\d{1,9}[.)])[ \t]+|>[ \t]*)*>"
_QUOTE_RE = re.compile(rf"[ \t]*{_QUOTE_PREFIX}")
_VIBED_QUOTE_RE = re.compile(
    rf"[ \t]*(?:{VIBE_TAG_RE.pattern}[^\S\r\n]+)*{_QUOTE_PREFIX}"
)
_OPERAND_RE = re.compile(r"[^\s<>!=]+")
_WRAPPERS = """'"`()[]{}*.,;:?!"""
_NUMBER_RE = re.compile(r"[-+\u2212]?(?:\d+(?:[.,]\d+)*|[.,]\d+)(?:[eE][+-]?\d+)?%?")
_RADIX_RE = re.compile(r"0(?:[xX][\da-fA-F]+|[bB][01]+|[oO][0-7]+)")


@final
class SpeechInput:
    """Prepare speech input without treating Markdown separators as operators."""

    @classmethod
    def prepare(cls, text: str, *, vibed: bool = False) -> str:
        """Rewrite Markdown labels, amounts, and same-line comparison operators."""
        prepared = cls._spell_around_links(text, vibed=vibed)
        return InlineCode.rewrite_currency(prepared, vibed=vibed)

    @classmethod
    def _spell_around_links(cls, text: str, *, vibed: bool) -> str:
        if "](" not in text:
            return cls._spell_text(text, vibed=vibed)
        parts: list[str] = []
        cursor = 0
        for span in SpeechMarkdown.spans(text, vibed=vibed):
            if isinstance(span, LinkSpan):
                opening, label_end, end = span
                parts.extend(
                    (
                        cls._spell_text(text[cursor:opening], vibed=vibed),
                        "[",
                        cls._spell_text(text[opening + 1 : label_end], vibed=vibed),
                        text[label_end:end],
                    )
                )
                cursor = end
        parts.append(cls._spell_text(text[cursor:], vibed=vibed))
        return "".join(parts)

    @classmethod
    def _spell_text(cls, text: str, *, vibed: bool) -> str:
        return "".join(
            cls._spell_line(line, vibed=vibed)
            for line in text.splitlines(keepends=True)
        )

    @classmethod
    def _spell_line(cls, line: str, *, vibed: bool) -> str:
        if not any(mark in line for mark in "<>=!"):
            return line
        words = cls._word_matches(line, vibed=vibed)
        parts: list[str] = []
        cursor = 0
        for index in cls._operators([word[0] for word in words]):
            word = words[index]
            parts.extend((line[cursor : word.start()], _SPOKEN[word[0]]))
            cursor = word.end()
        return "".join(parts) + line[cursor:]

    @staticmethod
    def _word_matches(line: str, *, vibed: bool) -> list[re.Match[str]]:
        quote = (_VIBED_QUOTE_RE if vibed else _QUOTE_RE).match(line)
        return [
            word
            for word in _WORD_RE.finditer(line, quote.end() if quote else 0)
            if not (vibed and VIBE_TAG_RE.fullmatch(word[0]))
        ]

    @classmethod
    def _operators(cls, words: list[str]) -> Iterator[int]:
        index = 1
        while index < len(words) - 1:
            token = words[index]
            if token in _BARE_ANGLES:
                end = cls._angle_chain_end(words, index + 2)
                if cls._ordered_operands(words[index - 1 : end : 2]):
                    yield from range(index, end, 2)
                index = end - 1
            elif token in _SPOKEN and all(
                cls._is_operand(value) for value in (words[index - 1], words[index + 1])
            ):
                yield index
            index += 1

    @staticmethod
    def _angle_chain_end(words: list[str], index: int) -> int:
        while index < len(words) - 1 and words[index] in _BARE_ANGLES:
            index += 2
        return index

    @classmethod
    def _ordered_operands(cls, operands: list[str]) -> bool:
        # A bare angle chain needs a numeric anchor and no prose-like operands.
        has_number = False
        for operand in operands:
            if not cls._is_operand(operand):
                return False
            value = operand.strip(_WRAPPERS)
            if cls._is_numeric(value):
                has_number = True
            elif len(value) != 1 or not value.isalpha():
                return False
        return has_number

    @staticmethod
    def _is_numeric(value: str) -> bool:
        return bool(
            _NUMBER_RE.fullmatch(value)
            or _RADIX_RE.fullmatch(value)
            or CurrencyGrammar.CODE_AMOUNT.fullmatch(value)
        )

    @staticmethod
    def _is_operand(value: str) -> bool:
        return (
            _OPERAND_RE.fullmatch(value.rstrip(_WRAPPERS)) is not None
            and VIBE_TAG_RE.fullmatch(value) is None
            and any(char.isalnum() for char in value)
        )
