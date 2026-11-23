"""Find matching backtick delimiters within a Markdown paragraph."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from typing import final

from punt_vox.markdown_fence import Fence

_BLOCK_LINE = re.compile(r"^ {0,3}(?:#{1,6}(?:[ \t]|$)|(?:=+|-+|(?:>[ \t]*)+)[ \t]*$)")
_LIST_ITEM = re.compile(r"^ {0,3}(?:[-+*]|[0-9]{1,9}[.)])[ \t]")
_BACKTICKS = re.compile(r"`+")


@final
class InlineCode:
    """Rewrite the content of matched backtick spans without touching prose."""

    @classmethod
    def rewrite(cls, text: str, transform: Callable[[str], str]) -> str:
        """Apply *transform* inside matched spans, leaving delimiters intact."""
        if "`" not in text:
            return text
        return "".join(
            cls._rewrite_paragraph(block, transform) if inline else block
            for block, inline in cls._blocks(text)
        )

    @staticmethod
    def _blocks(text: str) -> Iterator[tuple[str, bool]]:
        paragraph: list[str] = []
        fence: Fence | None = None
        for line in text.splitlines(keepends=True):
            content = line.rstrip("\r\n")
            delimiter = Fence.parse(content.rstrip(" \t"))
            if fence is not None:
                yield line, False
                if delimiter is not None and delimiter.closes(fence):
                    fence = None
            elif (
                delimiter is not None
                or not content.strip()
                or _BLOCK_LINE.match(content)
            ):
                yield "".join(paragraph), True
                paragraph.clear()
                yield line, delimiter is None
                fence = delimiter
            else:
                if _LIST_ITEM.match(content):
                    yield "".join(paragraph), True
                    paragraph.clear()
                paragraph.append(line)
        yield "".join(paragraph), True

    @classmethod
    def _rewrite_paragraph(cls, text: str, transform: Callable[[str], str]) -> str:
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
