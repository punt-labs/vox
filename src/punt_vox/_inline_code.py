"""Locate CommonMark inline-code spans in the original speech input."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from typing import final

from punt_vox._currency_grammar import CurrencyGrammar
from punt_vox._speech_markdown import VIBE_TAG_RE as VIBE_TAG_RE, SpeechMarkdown
from punt_vox._speech_punctuation import SpeechPunctuation

_WORD = re.compile(r"\S+")


@final
class InlineCode:
    """Rewrite inline code without re-rendering the Markdown around it."""

    @classmethod
    def rewrite(cls, text: str, transform: Callable[[str], str]) -> str:
        """Apply *transform* inside matched spans, leaving delimiters intact."""
        return cls._rewrite(text, lambda body, _: transform(body))

    @classmethod
    def rewrite_currency(cls, text: str, *, vibed: bool = False) -> str:
        """Canonicalize prose amounts and spell code signs literally outside paths."""
        tag = VIBE_TAG_RE if vibed else None
        return cls._rewrite(
            text,
            lambda body, in_path: SpeechPunctuation.spell_code_currency(
                body, in_path=in_path
            ),
            prose=lambda body: CurrencyGrammar.prefix_suffix_amounts(body, tag=tag),
            vibed=vibed,
        )

    @classmethod
    def prepare_speech(cls, text: str, *, vibed: bool = False) -> str:
        """Remove link destinations before rewriting currency for speech."""
        return cls.rewrite_currency(
            SpeechMarkdown.link_labels(text, vibed=vibed), vibed=vibed
        )

    @classmethod
    def _rewrite(
        cls,
        text: str,
        transform: Callable[[str, bool], str],
        *,
        prose: Callable[[str], str] = str,
        vibed: bool = False,
    ) -> str:
        """Transform prose separately from code, with path context for code."""
        if "`" not in text:
            return prose(text)
        parts: list[str] = []
        cursor = 0
        spans = SpeechMarkdown.code_spans(text, vibed=vibed)
        for in_path, start, end in cls._with_path_context(text, spans):
            parts.append(prose(text[cursor:start]))
            parts.append(transform(text[start:end], in_path))
            cursor = end
        parts.append(prose(text[cursor:]))
        return "".join(parts)

    @staticmethod
    def _with_path_context(
        text: str, spans: Iterator[tuple[int, int, int]]
    ) -> Iterator[tuple[bool, int, int]]:
        words = _WORD.finditer(text)
        word = next(words)
        in_path: bool | None = None
        for opening, start, end in spans:
            while word.end() <= opening:
                word = next(words)
                in_path = None
            if in_path is None:
                in_path = SpeechPunctuation.is_code_path(text[word.start() : opening])
            yield in_path, start, end
