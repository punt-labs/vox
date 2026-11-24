"""Locate CommonMark inline-code spans in the original speech input."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Iterator
from typing import final

from punt_vox._currency_grammar import CurrencyGrammar
from punt_vox._link_labels import VIBE_TAG_RE, LinkLabels, LinkSpan
from punt_vox._speech_markdown import CodeSpan, SpeechMarkdown
from punt_vox._speech_punctuation import SpeechPunctuation

_WORD = re.compile(r"\S+")


@final
class InlineCode:
    """Rewrite inline code without re-rendering the Markdown around it."""

    @classmethod
    def rewrite(cls, text: str, transform: Callable[[str], str]) -> str:
        """Apply *transform* inside matched spans, leaving delimiters intact."""
        return cls._rewrite(
            text,
            cls._code(SpeechMarkdown.spans(text, vibed=False)),
            lambda body, _: transform(body),
            lambda start, end: text[start:end],
        )

    @classmethod
    def rewrite_currency(cls, text: str, *, vibed: bool = False) -> str:
        """Prepare Markdown link labels and distinguish prose from code amounts."""
        tag = VIBE_TAG_RE if vibed else None
        spans = tuple(SpeechMarkdown.spans(text, vibed=vibed))
        labels = LinkLabels([span for span in spans if isinstance(span, LinkSpan)])
        return cls._rewrite(
            text,
            cls._code(spans),
            lambda body, in_path: SpeechPunctuation.spell_code_currency(
                body, in_path=in_path
            ).replace("](", "] ("),
            lambda start, end: CurrencyGrammar.prefix_suffix_amounts(
                labels.prose(text, start, end), tag=tag
            ),
        )

    @staticmethod
    def _code(spans: Iterable[CodeSpan | LinkSpan]) -> Iterator[CodeSpan]:
        return (span for span in spans if isinstance(span, CodeSpan))

    @classmethod
    def _rewrite(
        cls,
        text: str,
        spans: Iterator[CodeSpan],
        transform: Callable[[str, bool], str],
        prose: Callable[[int, int], str],
    ) -> str:
        """Transform prose separately from code, with path context for code."""
        parts: list[str] = []
        cursor = 0
        for in_path, start, end in cls._with_path_context(text, spans):
            parts.append(prose(cursor, start))
            parts.append(transform(text[start:end], in_path))
            cursor = end
        parts.append(prose(cursor, len(text)))
        return "".join(parts)

    @staticmethod
    def _with_path_context(
        text: str, spans: Iterator[CodeSpan]
    ) -> Iterator[tuple[bool, int, int]]:
        words = _WORD.finditer(text)
        word = None
        in_path: bool | None = None
        for opening, start, end in spans:
            while word is None or word.end() <= opening:
                word = next(words)
                in_path = None
            if in_path is None:
                in_path = SpeechPunctuation.is_code_path(text[word.start() : opening])
            yield in_path, start, end
