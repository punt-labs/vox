"""Locate inline Markdown syntax without re-rendering surrounding prose."""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple, cast, final

from markdown_it.rules_inline import backtick

from punt_vox._markdown_offsets import MarkdownOffsets
from punt_vox._speech_parser import ALT_OPENING, SOURCE_OFFSET, LinkSpan, SpeechParser

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from markdown_it.rules_inline import StateInline
    from markdown_it.token import Token


class CodeSpan(NamedTuple):
    opening: int
    start: int
    end: int


@final
class SpeechMarkdown:
    """Parse speech Markdown and retain offsets into the original input."""

    @classmethod
    def spans(cls, text: str, *, vibed: bool) -> Iterator[CodeSpan | LinkSpan]:
        """Yield code, link, and image bounds from the original Markdown parse."""
        if "`" not in text and "](" not in text:
            return
        for content, children, lines in cls._inline_sources(text, vibed=vibed):
            offsets = MarkdownOffsets.from_content(text, lines, content)
            for child, relative in cls._source_tokens(children):
                if span := cls._source_span(child, relative, offsets):
                    yield span

    @staticmethod
    def _source_span(
        token: Token, offset: int, positions: dict[int, int]
    ) -> CodeSpan | LinkSpan | None:
        at = positions.get
        if token.type == "code_inline":
            opening, end = cast("tuple[int, int]", token.meta[SOURCE_OFFSET])
            width = len(token.markup)
            code = at(offset + opening), at(offset + end - width)
            if None not in code:
                start, close = cast("tuple[int, int]", code)
                return CodeSpan(start, start + width, close)
        elif isinstance(bounds := token.meta.get(SOURCE_OFFSET), tuple):
            opening, label_end, end = cast("tuple[int, int, int]", bounds)
            return SpeechMarkdown._label_span(
                (at(offset + opening), at(offset + label_end), at(offset + end)),
                image=token.type == "image",
            )
        return None

    @staticmethod
    def _label_span(
        bounds: tuple[int | None, int | None, int | None], *, image: bool
    ) -> LinkSpan | None:
        if None in bounds:
            return None
        start, label_end, close = cast("tuple[int, int, int]", bounds)
        return LinkSpan((start - image, start + 1), label_end, close + 1)

    @classmethod
    def _inline_sources(
        cls, text: str, *, vibed: bool
    ) -> Iterator[tuple[str, list[Token], list[int]]]:
        lines = MarkdownOffsets.line_boundaries(text)
        for token in cls._MARKDOWN.parse_speech(text, vibed=vibed):
            if token.type == "inline" and token.map is not None and token.children:
                start, end = token.map
                yield token.content, token.children, lines[start : end + 1]

    @classmethod
    def _source_tokens(
        cls, tokens: Sequence[Token], offset: int = 0
    ) -> Iterator[tuple[Token, int]]:
        for token in tokens:
            yield token, offset
            if token.type == "image" and token.children:
                start = cast("int", token.meta[ALT_OPENING])
                yield from cls._source_tokens(token.children, offset + start)

    @staticmethod
    def _code_rule(state: StateInline, *, silent: bool) -> bool:
        start, size = state.pos, len(state.tokens)
        matched = backtick(state, silent)
        if matched and len(state.tokens) > size:
            state.tokens[-1].meta[SOURCE_OFFSET] = (start, state.pos)
        return matched

    _MARKDOWN = SpeechParser.create()
    _MARKDOWN.inline.ruler.at(
        "backticks",
        lambda state, silent: SpeechMarkdown._code_rule(state, silent=silent),
    )
