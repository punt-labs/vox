"""Find matching backtick delimiters within CommonMark inline blocks."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import final

from markdown_it import MarkdownIt

_MARKDOWN_BLOCKS = MarkdownIt("commonmark").disable("inline")
_LINE_END = re.compile(r"\r\n?|\n")
_BACKTICKS = re.compile(r"`+")


@final
class InlineCode:
    """Rewrite the content of matched backtick spans without touching prose."""

    @classmethod
    def rewrite(cls, text: str, transform: Callable[[str], str]) -> str:
        """Apply *transform* inside matched spans, leaving delimiters intact."""
        if "`" not in text:
            return text
        offsets = [0, *(end.end() for end in _LINE_END.finditer(text)), len(text)]
        parts: list[str] = []
        cursor = 0
        for token in _MARKDOWN_BLOCKS.parse(text):
            if token.type == "inline" and token.map is not None:
                start, end = (offsets[line] for line in token.map)
                parts.extend(
                    (text[cursor:start], cls._rewrite_block(text[start:end], transform))
                )
                cursor = end
        parts.append(text[cursor:])
        return "".join(parts)

    @classmethod
    def _rewrite_block(cls, text: str, transform: Callable[[str], str]) -> str:
        runs = tuple(_BACKTICKS.finditer(text))
        closers = cls._closers(text, runs)
        parts: list[str] = []
        cursor = 0
        for run, closer in zip(runs, closers, strict=True):
            if run.start() < cursor or closer is None:
                continue
            parts.extend(
                (text[cursor : run.end()], transform(text[run.end() : closer.start()]))
            )
            cursor = closer.end()
            parts.append(closer[0])
        parts.append(text[cursor:])
        return "".join(parts)

    @classmethod
    def _closers(
        cls, text: str, runs: tuple[re.Match[str], ...]
    ) -> list[re.Match[str] | None]:
        next_by_width: dict[int, re.Match[str]] = {}
        closers: list[re.Match[str] | None] = []
        for run in reversed(runs):
            width = len(run[0])
            opener_width = width - cls._escapes_first_tick(text, run.start())
            closers.append(next_by_width.get(opener_width))
            next_by_width[width] = run
        closers.reverse()
        return closers

    @staticmethod
    def _escapes_first_tick(text: str, start: int) -> bool:
        before = start
        while before and text[before - 1] == "\\":
            before -= 1
        return (start - before) % 2 == 1
