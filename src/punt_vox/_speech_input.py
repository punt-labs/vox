"""Preserve standalone comparisons before normalizing speech words."""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import NamedTuple, final

from punt_vox._currency_grammar import SCALES, UNITS, CurrencyGrammar
from punt_vox._currency_prose import ProseCurrency
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
_WORD_RE = re.compile(r"\S+")
_STANDALONE_RE = re.compile(rf"(?<!\S)(?:{'|'.join(map(re.escape, _SPOKEN))})(?!\S)")
_VIBED_WORD_RE = re.compile(
    rf"(?P<tag>{VIBE_TAG_RE.pattern})|(?:(?!{VIBE_TAG_RE.pattern})\S)+"
)
_QUOTE_PREFIX = r"(?:(?:[-+*]|\d{1,9}[.)])[ \t]+|>[ \t]*)*>"
_QUOTE_RE = re.compile(rf"[ \t]*{_QUOTE_PREFIX}")
_VIBED_QUOTE_RE = re.compile(
    rf"[ \t]*(?:{VIBE_TAG_RE.pattern}[^\S\r\n]+)*{_QUOTE_PREFIX}"
)
_OPERAND_RE = re.compile(r"[^\s<>!=]+")
_WRAPPERS = """'"`()[]{}*_.,;:?!\u2018\u2019\u201c\u201d"""
_NUMBER_RE = re.compile(r"[-+\u2212]?(?:\d+(?:[.,]\d+)*|[.,]\d+)(?:[eE][+-]?\d+)?%?")
_RADIX_RE = re.compile(r"0(?:[xX][\da-fA-F]+|[bB][01]+|[oO][0-7]+)")
_CURRENCY_RE = re.compile(f"[{''.join(UNITS)}]")


class _Word(NamedTuple):
    value: str
    start: int
    end: int


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
        depth = 0
        for start, end, change in cls._link_edges(text, vibed=vibed):
            parts.append(
                cls._spell_segment(
                    text[cursor:start],
                    vibed=vibed,
                    label=depth > 0,
                    follows_link=cursor > 0,
                    precedes_link=True,
                )
            )
            parts.append(text[start:end])
            cursor = end
            depth += change
        parts.append(
            cls._spell_segment(text[cursor:], vibed=vibed, follows_link=cursor > 0)
        )
        return "".join(parts)

    @staticmethod
    def _link_edges(text: str, *, vibed: bool) -> list[tuple[int, int, int]]:
        return sorted(
            edge
            for span in SpeechMarkdown.spans(text, vibed=vibed)
            if isinstance(span, LinkSpan)
            for edge in (
                (span.opening, span.opening + 1, 1),
                (span.label_end, span.end, -1),
            )
        )

    @classmethod
    def _spell_segment(
        cls,
        text: str,
        *,
        vibed: bool,
        follows_link: bool,
        label: bool = False,
        precedes_link: bool = False,
    ) -> str:
        # A link boundary is not an operand but must break a chain like a word.
        prefix = "... " if follows_link else ""
        suffix = " ..." if precedes_link else ""
        spelled = cls._spell_text(f"{prefix}{text}{suffix}", vibed=vibed, label=label)
        return spelled[len(prefix) : len(spelled) - len(suffix)]

    @classmethod
    def _spell_text(cls, text: str, *, vibed: bool, label: bool = False) -> str:
        return "".join(
            cls._spell_line(line, vibed=vibed, label=label)
            for line in text.splitlines(keepends=True)
        )

    @classmethod
    def _spell_line(cls, line: str, *, vibed: bool, label: bool) -> str:
        if not any(mark in line for mark in "<>=!"):
            return line
        words = cls._word_matches(line, vibed=vibed and not label, label=label)
        parts: list[str] = []
        cursor = 0
        for index in cls._operators([word.value for word in words]):
            word = words[index]
            parts.extend((line[cursor : word.start], _SPOKEN[word.value]))
            cursor = word.end
        return "".join(parts) + line[cursor:]

    @classmethod
    def _word_matches(cls, line: str, *, vibed: bool, label: bool) -> list[_Word]:
        quote = (_VIBED_QUOTE_RE if vibed else _QUOTE_RE).match(line)
        pattern = _VIBED_WORD_RE if vibed else _WORD_RE
        words: list[_Word] = []
        for word in pattern.finditer(line, quote.end() if quote else 0):
            if vibed and word["tag"] is not None:
                continue
            if words and cls._extends_amount(words[-1].value, word[0]):
                previous = words.pop()
                words.append(
                    _Word(f"{previous.value} {word[0]}", previous.start, word.end())
                )
            else:
                words.append(cls._comparison_word(line, word, label=label))
        return words

    @staticmethod
    def _comparison_word(line: str, word: re.Match[str], *, label: bool) -> _Word:
        value = word[0]
        if label and (bracketed := VIBE_TAG_RE.fullmatch(value)):
            value = bracketed[1]
        elif value in _SPOKEN and _STANDALONE_RE.match(line, word.start()) is None:
            value = ""
        return _Word(value, word.start(), word.end())

    @classmethod
    def _extends_amount(cls, amount: str, word: str) -> bool:
        scale = word.strip(_WRAPPERS).lower()
        return (
            len(scale) > 1 and scale in SCALES and cls._is_currency(f"{amount} {word}")
        )

    @classmethod
    def _operators(cls, words: list[str]) -> Iterator[int]:
        index = 1
        while index < len(words) - 1:
            if words[index] in _SPOKEN:
                end = cls._chain_end(words, index + 2)
                yield from cls._chain_operators(words, index, end)
                index = end - 1
            index += 1

    @staticmethod
    def _chain_end(words: list[str], index: int) -> int:
        while index < len(words) - 1 and words[index] in _SPOKEN:
            index += 2
        return index

    @classmethod
    def _chain_operators(cls, words: list[str], start: int, end: int) -> Iterator[int]:
        indices = range(start, end, 2)
        if cls._anchored_operands(words[start - 1 : end : 2]):
            yield from indices
        else:
            yield from (
                index for index in indices if cls._compound_comparison(words, index)
            )

    @classmethod
    def _compound_comparison(cls, words: list[str], index: int) -> bool:
        return len(words[index]) == 2 and all(
            cls._is_operand(value) for value in (words[index - 1], words[index + 1])
        )

    @classmethod
    def _anchored_operands(cls, operands: list[str]) -> bool:
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

    @classmethod
    def _is_numeric(cls, value: str) -> bool:
        return bool(
            _NUMBER_RE.fullmatch(value)
            or _RADIX_RE.fullmatch(value)
            or cls._is_currency(value)
        )

    @staticmethod
    def _is_currency(value: str) -> bool:
        if _CURRENCY_RE.search(value) is None:
            return False
        amount = ProseCurrency.canonicalize(value.strip(_WRAPPERS), tag=None)
        return CurrencyGrammar.prose_pattern(amount).fullmatch(amount) is not None

    @classmethod
    def _is_operand(cls, value: str) -> bool:
        if " " in value:
            return cls._is_currency(value)
        return (
            _OPERAND_RE.fullmatch(value.rstrip(_WRAPPERS)) is not None
            and VIBE_TAG_RE.fullmatch(value) is None
            and any(char.isalnum() for char in value)
        )
