"""Locate inline Markdown syntax without re-rendering surrounding prose."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, cast, final, override

from markdown_it import MarkdownIt
from markdown_it.rules_block import fence
from markdown_it.rules_inline import backtick, image, link

from punt_vox._markdown_offsets import MarkdownOffsets

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from markdown_it.rules_block import StateBlock
    from markdown_it.rules_inline import StateInline
    from markdown_it.token import Token

_SOURCE_OFFSET = "vox_source_offset"
_VIBED = "vox_vibed"
VIBE_TAG_RE = re.compile(r"\[([a-z]+)\]")
_FENCE_TAGS = re.compile(rf"(?:{VIBE_TAG_RE.pattern}[ \t]*)+(?=`{{3,}}|~{{3,}})")


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
    def link_labels(cls, text: str, *, vibed: bool) -> str:
        """Keep inline link labels in prose and drop their destination syntax."""
        if "](" not in text:
            return text
        parts: list[str] = []
        cursor = 0
        removals = (
            bounds
            for left, label_end, right in cls._link_spans(text, vibed=vibed)
            for bounds in ((left, left + 1), (label_end, right + 1))
        )
        for start, end in sorted(removals):
            parts.append(text[cursor:start])
            cursor = end
        parts.append(text[cursor:])
        return "".join(parts)

    @classmethod
    def _link_spans(cls, text: str, *, vibed: bool) -> Iterator[tuple[int, int, int]]:
        for token, lines in cls._inline_sources(text, vibed=vibed):
            offsets = MarkdownOffsets.from_content(text, lines, token.content)
            for child, relative in cls._source_tokens(token.children or ()):
                if child.type != "link_open" or _SOURCE_OFFSET not in child.meta:
                    continue
                left, label_end, right = cast(
                    "tuple[int, int, int]", child.meta[_SOURCE_OFFSET]
                )
                left, label_end, right = (
                    relative + left,
                    relative + label_end,
                    relative + right,
                )
                if left in offsets and label_end in offsets and right in offsets:
                    yield offsets[left], offsets[label_end], offsets[right]

    @classmethod
    def code_spans(cls, text: str, *, vibed: bool) -> Iterator[tuple[int, int, int]]:
        """Yield the opening delimiter and body boundaries of inline code."""
        for token, lines in cls._inline_sources(text, vibed=vibed):
            spans = tuple(cls._code_spans(token.children or ()))
            if not spans:
                continue
            ticks = MarkdownOffsets.from_content(text, lines, token.content)
            for opening, closing, width in spans:
                if opening in ticks and closing in ticks:
                    yield ticks[opening], ticks[opening] + width, ticks[closing]

    @classmethod
    def _inline_sources(
        cls, text: str, *, vibed: bool
    ) -> Iterator[tuple[Token, list[int]]]:
        lines = MarkdownOffsets.line_boundaries(text)
        for token in cls._MARKDOWN.parse(text, {_VIBED: vibed}):
            if token.type == "inline" and token.map is not None and token.children:
                yield token, lines[token.map[0] : token.map[1] + 1]

    @classmethod
    def _code_spans(cls, tokens: Sequence[Token]) -> Iterator[tuple[int, int, int]]:
        for token, offset in cls._source_tokens(tokens):
            if token.type == "code_inline":
                opening, end = cast("tuple[int, int]", token.meta[_SOURCE_OFFSET])
                width = len(token.markup)
                yield offset + opening, offset + end - width, width

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
