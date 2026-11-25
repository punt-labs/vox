"""Render original prose slices while dropping inline link destinations."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, NamedTuple, Self, final

from markdown_it.common.utils import unescapeAll

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

VIBE_TAG_RE = re.compile(r"\[([a-z]+)\]")


class LinkSpan(NamedTuple):
    opening: int
    label_end: int
    end: int


@final
class LinkLabels:
    """Track which source slices are link labels, not surrounding prose."""

    _boundaries: Iterator[tuple[int, int, int]]
    _next: tuple[int, int, int] | None
    _depth: int

    def __new__(cls, links: Sequence[LinkSpan]) -> Self:
        self = super().__new__(cls)
        self._boundaries = iter(
            sorted(
                boundary
                for opening, label_end, end in links
                for boundary in ((opening, opening + 1, 1), (label_end, end, -1))
            )
        )
        self._next = next(self._boundaries, None)
        self._depth = 0
        return self

    def prose(self, text: str, start: int, stop: int) -> str:
        parts: list[str] = []
        while self._next is not None and self._next[0] < stop:
            before, after, depth_change = self._next
            parts.append(self._decode(text[start:before]))
            self._depth += depth_change
            self._next = next(self._boundaries, None)
            if self._next == (after, after + 1, 1):
                parts.append(" ")
            start = after
        parts.append(self._decode(text[start:stop]))
        return "".join(parts)

    def _decode(self, prose: str) -> str:
        if self._depth:
            return VIBE_TAG_RE.sub(r"\1", unescapeAll(prose)).replace("](", "] (")
        return prose
