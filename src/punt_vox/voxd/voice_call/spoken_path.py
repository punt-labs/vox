"""One repository path and how many of its trailing segments are spoken."""

from __future__ import annotations

from pathlib import PurePath
from typing import Self, final

__all__ = ["SpokenPath"]


@final
class SpokenPath:
    """One repository path and how many of its trailing segments are spoken."""

    __slots__ = ("_depth", "_segments")
    _depth: int
    _segments: tuple[str, ...]

    def __new__(cls, project: str) -> Self:
        """Keep the path's segments without its root; speak the last one first."""
        self = super().__new__(cls)
        path = PurePath(project)
        self._segments = tuple(p for p in path.parts if p != path.anchor) or (
            str(path),
        )
        self._depth = 1
        return self

    def spoken(self) -> str:
        """Return the last ``depth`` segments joined by spaces, never slashes."""
        return " ".join(self._segments[-self._depth :])

    def deepen(self) -> bool:
        """Speak one more parent segment; return whether there was one to add."""
        if self._depth >= len(self._segments):
            return False
        self._depth += 1
        return True
