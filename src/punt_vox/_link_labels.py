"""Render original prose slices while dropping inline Markdown destinations."""

from __future__ import annotations

import re
from itertools import chain
from sys import maxsize
from typing import TYPE_CHECKING, NamedTuple, Self, final

from markdown_it.common.utils import unescapeAll

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

VIBE_TAG_RE = re.compile(r"\[([a-z]+)\]")
_END = (maxsize, maxsize, 0)


class LinkSpan(NamedTuple):
    prefix: tuple[int, int]
    label_end: int
    end: int


@final
class LinkLabels:
    """Track which source slices are link labels or image alt text."""

    _boundaries: Iterator[tuple[int, int, int]]
    _next: tuple[int, int, int]
    _depth: int

    def __new__(cls, links: Sequence[LinkSpan]) -> Self:
        self = super().__new__(cls)
        boundaries = chain.from_iterable(
            ((*prefix, 1), (label_end, end, -1)) for prefix, label_end, end in links
        )
        self._boundaries = iter(sorted(boundaries))
        self._next = next(self._boundaries, _END)
        self._depth = 0
        return self

    def prose(self, text: str, start: int, stop: int) -> str:
        parts: list[str] = []
        while self._next[0] < stop:
            before, after, depth_change = self._next
            parts.append(self._decode(text[start:before]))
            self._depth += depth_change
            self._next = next(self._boundaries, _END)
            if (*self._next[::2], depth_change) == (after, 1, -1):
                parts.append(" ")
            start = after
        parts.append(self._decode(text[start:stop]))
        return "".join(parts)

    def _decode(self, prose: str) -> str:
        if self._depth:
            return VIBE_TAG_RE.sub(r"\1", unescapeAll(prose)).replace("](", "] (")
        return prose
