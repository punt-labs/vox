"""Find matching backtick delimiters within a Markdown paragraph."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import final

_PARAGRAPH_BREAK = re.compile(r"(\r?\n[ \t]*\r?\n)")
_BACKTICKS = re.compile(r"`+")


@final
class InlineCode:
    """Rewrite the content of matched backtick spans without touching prose."""

    @classmethod
    def rewrite(cls, text: str, transform: Callable[[str], str]) -> str:
        """Apply *transform* inside matched spans, leaving delimiters intact."""
        paragraphs = _PARAGRAPH_BREAK.split(text)
        paragraphs[::2] = [
            cls._rewrite_paragraph(paragraph, transform)
            for paragraph in paragraphs[::2]
        ]
        return "".join(paragraphs)

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
