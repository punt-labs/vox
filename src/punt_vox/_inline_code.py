"""Locate CommonMark inline-code spans in the original speech input."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Sequence
from itertools import dropwhile, pairwise
from typing import TYPE_CHECKING, cast, final

from markdown_it import MarkdownIt
from markdown_it.rules_inline import backtick, image

from punt_vox._speech_punctuation import SpeechPunctuation

if TYPE_CHECKING:
    from markdown_it.rules_inline import StateInline
    from markdown_it.token import Token

_LINE_END = re.compile(r"\r\n?|\n")
_TICK = re.compile("`")
_SOURCE_OFFSET = "vox_source_offset"
VIBE_TAG_RE = re.compile(r"\[([a-z]+)\]")
_VIBE_BEFORE_FENCE = re.compile(
    rf"(?<![^\r\n])( {{0,3}})(?:{VIBE_TAG_RE.pattern}[ \t]*)+(?=`{{3,}}|~{{3,}})"
)


@final
class InlineCode:
    """Rewrite inline code without re-rendering the Markdown around it."""

    @classmethod
    def rewrite(cls, text: str, transform: Callable[[str], str]) -> str:
        """Apply *transform* inside matched spans, leaving delimiters intact."""
        return cls._rewrite(text, lambda body, _: transform(body))

    @classmethod
    def rewrite_currency(cls, text: str, *, vibed: bool = False) -> str:
        """Spell code currency signs literally, except in paths."""
        return cls._rewrite(text, SpeechPunctuation.spell_code_currency, vibed=vibed)

    @classmethod
    def _rewrite(
        cls,
        text: str,
        transform: Callable[[str, str], str],
        *,
        vibed: bool = False,
    ) -> str:
        """Pass each span's body and adjacent token prefix to *transform*."""
        if "`" not in text:
            return text
        lines = [0, *(end.end() for end in _LINE_END.finditer(text)), len(text)]
        parts: list[str] = []
        cursor = 0
        markdown = _VIBE_BEFORE_FENCE.sub(r"\1", text) if vibed else text
        for opening, start, end in cls._source_spans(text, lines, markdown):
            prefix = opening
            while prefix > 0 and not text[prefix - 1].isspace():
                prefix -= 1
            parts.extend(
                (text[cursor:start], transform(text[start:end], text[prefix:opening]))
            )
            cursor = end
        parts.append(text[cursor:])
        return "".join(parts)

    @classmethod
    def _source_spans(
        cls, text: str, lines: Sequence[int], markdown: str
    ) -> Iterator[tuple[int, int, int]]:
        for token in cls._MARKDOWN.parse(markdown):
            if token.type != "inline" or token.map is None or not token.children:
                continue
            spans = tuple(cls._spans(token.children))
            if not spans:
                continue
            ticks = cls._source_ticks(
                text, lines[token.map[0] : token.map[1] + 1], token.content
            )
            for opening, closing, width in spans:
                if opening in ticks and closing in ticks:
                    yield ticks[opening], ticks[opening] + width, ticks[closing]

    @staticmethod
    def _source_ticks(text: str, lines: Sequence[int], content: str) -> dict[int, int]:
        """Map inline backticks to the raw text, skipping block prefixes."""
        ticks: dict[int, int] = {}
        position = 0
        raw_lines = (
            (start, text[start:end].replace("\0", "\ufffd"))
            for start, end in pairwise(lines)
        )
        content_lines = content.split("\n")
        first = content_lines[0].lstrip()
        aligned = dropwhile(lambda entry: first not in entry[1], raw_lines)
        for (start, raw), line in zip(aligned, content_lines, strict=False):
            piece = line.lstrip()
            column = raw.find(piece)
            if column >= 0:
                padding = len(line) - len(piece)
                ticks.update(
                    (position + padding + tick.start(), start + column + tick.start())
                    for tick in _TICK.finditer(piece)
                )
            position += len(line) + 1
        return ticks

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

    _MARKDOWN = MarkdownIt("commonmark")
    _MARKDOWN.inline.ruler.at(
        "backticks", lambda state, silent: InlineCode._code_rule(state, silent=silent)
    )
    _MARKDOWN.inline.ruler.at(
        "image", lambda state, silent: InlineCode._image_rule(state, silent=silent)
    )
