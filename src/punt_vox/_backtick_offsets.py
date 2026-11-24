"""Translate CommonMark inline backtick offsets into speech source offsets."""

from __future__ import annotations

import re
from itertools import dropwhile, pairwise
from typing import TYPE_CHECKING, final

if TYPE_CHECKING:
    from collections.abc import Sequence

_TICK = re.compile("`")
_LINE_END = re.compile(r"\r\n?|\n")


@final
class BacktickOffsets:
    """Align parsed inline content with its original Markdown lines."""

    @staticmethod
    def line_boundaries(text: str) -> list[int]:
        """Return raw line boundaries before CommonMark normalizes newlines."""
        return [0, *(end.end() for end in _LINE_END.finditer(text)), len(text)]

    @staticmethod
    def from_content(text: str, lines: Sequence[int], content: str) -> dict[int, int]:
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
