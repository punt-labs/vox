"""Locate CommonMark inline-code spans in the original speech input."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Sequence
from typing import TYPE_CHECKING, cast, final

from markdown_it import MarkdownIt
from markdown_it.rules_block import fence
from markdown_it.rules_inline import backtick, image

from punt_vox._backtick_offsets import BacktickOffsets
from punt_vox._currency_grammar import CurrencyGrammar
from punt_vox._speech_punctuation import SpeechPunctuation

if TYPE_CHECKING:
    from markdown_it.rules_block import StateBlock
    from markdown_it.rules_inline import StateInline
    from markdown_it.token import Token

_WORD = re.compile(r"\S+")
_SOURCE_OFFSET = "vox_source_offset"
_VIBED = "vox_vibed"
VIBE_TAG_RE = re.compile(r"\[([a-z]+)\]")
_FENCE_TAGS = re.compile(rf"(?:{VIBE_TAG_RE.pattern}[ \t]*)+(?=`{{3,}}|~{{3,}})")


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
        return cls._rewrite(
            text,
            lambda body, in_path: SpeechPunctuation.spell_code_currency(
                body, in_path=in_path
            ),
            prose=CurrencyGrammar.prefix_suffix_amounts,
            vibed=vibed,
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
        spans = cls._source_spans(text, vibed=vibed)
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

    @classmethod
    def _source_spans(cls, text: str, *, vibed: bool) -> Iterator[tuple[int, int, int]]:
        lines = BacktickOffsets.line_boundaries(text)
        for token in cls._MARKDOWN.parse(text, {_VIBED: vibed}):
            if token.type != "inline" or token.map is None or not token.children:
                continue
            spans = tuple(cls._spans(token.children))
            if not spans:
                continue
            ticks = BacktickOffsets.from_content(
                text, lines[token.map[0] : token.map[1] + 1], token.content
            )
            for opening, closing, width in spans:
                if opening in ticks and closing in ticks:
                    yield ticks[opening], ticks[opening] + width, ticks[closing]

    @classmethod
    def _spans(
        cls, tokens: Sequence[Token], offset: int = 0
    ) -> Iterator[tuple[int, int, int]]:
        for token in tokens:
            if token.type == "code_inline":
                opening, end = cast("tuple[int, int]", token.meta[_SOURCE_OFFSET])
                width = len(token.markup)
                yield offset + opening, offset + end - width, width
            elif token.type == "image" and token.children:
                start = cast("int", token.meta[_SOURCE_OFFSET])
                yield from cls._spans(token.children, offset + start + 2)

    @staticmethod
    def _code_rule(state: StateInline, *, silent: bool) -> bool:
        start, size = state.pos, len(state.tokens)
        matched = backtick(state, silent)
        if matched and len(state.tokens) > size:
            state.tokens[-1].meta[_SOURCE_OFFSET] = (start, state.pos)
        return matched

    @staticmethod
    def _image_rule(state: StateInline, *, silent: bool) -> bool:
        start = state.pos
        matched = image(state, silent)
        if matched and not silent:
            state.tokens[-1].meta[_SOURCE_OFFSET] = start
        return matched

    @staticmethod
    def _fence_rule(
        state: StateBlock, start_line: int, end_line: int, *, silent: bool
    ) -> bool:
        if not state.env.get(_VIBED):
            return fence(state, start_line, end_line, silent)
        shift = state.tShift[start_line]
        start = state.bMarks[start_line] + shift
        tags = _FENCE_TAGS.match(state.src, start, state.eMarks[start_line])
        if tags is None:
            return fence(state, start_line, end_line, silent)
        state.tShift[start_line] += tags.end() - start
        try:
            return fence(state, start_line, end_line, silent)
        finally:
            state.tShift[start_line] = shift

    _MARKDOWN = MarkdownIt("commonmark")
    _MARKDOWN.block.ruler.at(
        "fence",
        lambda state, start, end, silent: InlineCode._fence_rule(
            state, start, end, silent=silent
        ),
        {"alt": ["paragraph", "reference", "blockquote", "list"]},
    )
    _MARKDOWN.inline.ruler.at(
        "backticks", lambda state, silent: InlineCode._code_rule(state, silent=silent)
    )
    _MARKDOWN.inline.ruler.at(
        "image", lambda state, silent: InlineCode._image_rule(state, silent=silent)
    )
