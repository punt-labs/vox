"""Locate inline Markdown syntax without re-rendering surrounding prose."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, NamedTuple, cast, final, override

from markdown_it import MarkdownIt
from markdown_it.rules_block import fence
from markdown_it.rules_inline import backtick, image, link

from punt_vox._link_labels import VIBE_TAG_RE, LinkSpan
from punt_vox._markdown_offsets import MarkdownOffsets

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from markdown_it.rules_block import StateBlock
    from markdown_it.rules_inline import StateInline
    from markdown_it.token import Token

_SOURCE_OFFSET = "vox_source_offset"
_VIBED = "vox_vibed"
_FENCE_TAGS = re.compile(rf"(?:{VIBE_TAG_RE.pattern}[ \t]*)+(?=`{{3,}}|~{{3,}})")


class CodeSpan(NamedTuple):
    opening: int
    start: int
    end: int


@final
class _SpeechParser(MarkdownIt):
    """Parse local file links for speech without opening their destinations."""

    @override
    def validateLink(self, url: str) -> bool:
        """Allow local file links while keeping the parser's other URL rules."""
        return url.lower().startswith("file:") or super().validateLink(url)


@final
class SpeechMarkdown:
    """Parse speech Markdown and retain offsets into the original input."""

    @classmethod
    def spans(cls, text: str, *, vibed: bool) -> Iterator[CodeSpan | LinkSpan]:
        """Yield code and link bounds from the original Markdown parse."""
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
        match token.type, token.meta.get(_SOURCE_OFFSET):
            case "code_inline", _:
                opening, end = cast("tuple[int, int]", token.meta[_SOURCE_OFFSET])
                width = len(token.markup)
                match at(offset + opening), at(offset + end - width):
                    case int(start), int(close):
                        return CodeSpan(start, start + width, close)
            case "link_open", (int(opening), int(label_end), int(end)):
                match at(offset + opening), at(offset + label_end), at(offset + end):
                    case int(start), int(label_stop), int(close):
                        return LinkSpan(start, label_stop, close + 1)
        return None

    @classmethod
    def _inline_sources(
        cls, text: str, *, vibed: bool
    ) -> Iterator[tuple[str, list[Token], list[int]]]:
        lines = MarkdownOffsets.line_boundaries(text)
        for token in cls._MARKDOWN.parse(text, {_VIBED: vibed}):
            if token.type == "inline" and token.map is not None and token.children:
                yield (
                    token.content,
                    token.children,
                    lines[token.map[0] : token.map[1] + 1],
                )

    @classmethod
    def _source_tokens(
        cls, tokens: Sequence[Token], offset: int = 0
    ) -> Iterator[tuple[Token, int]]:
        for token in tokens:
            if token.type == "image" and token.children:
                start = cast("int", token.meta[_SOURCE_OFFSET])
                yield from cls._source_tokens(token.children, offset + start + 2)
            else:
                yield token, offset

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
    def _link_rule(state: StateInline, *, silent: bool) -> bool:
        start = state.pos
        opening_token = len(state.tokens) + bool(state.pending)
        matched = link(state, silent)
        if matched and not silent:
            label_end = state.md.helpers.parseLinkLabel(
                state, start, disableNested=True
            )
            if state.pos > label_end + 2 and state.src[label_end + 1] == "(":
                state.tokens[opening_token].meta[_SOURCE_OFFSET] = (
                    start,
                    label_end,
                    state.pos - 1,
                )
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

    _MARKDOWN = _SpeechParser("commonmark")
    _MARKDOWN.block.ruler.at(
        "fence",
        lambda state, start, end, silent: SpeechMarkdown._fence_rule(
            state, start, end, silent=silent
        ),
        {"alt": ["paragraph", "reference", "blockquote", "list"]},
    )
    _MARKDOWN.inline.ruler.at(
        "backticks",
        lambda state, silent: SpeechMarkdown._code_rule(state, silent=silent),
    )
    _MARKDOWN.inline.ruler.at(
        "image", lambda state, silent: SpeechMarkdown._image_rule(state, silent=silent)
    )
    _MARKDOWN.inline.ruler.at(
        "link", lambda state, silent: SpeechMarkdown._link_rule(state, silent=silent)
    )
