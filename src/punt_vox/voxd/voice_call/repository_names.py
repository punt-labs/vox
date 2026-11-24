"""Spoken names for the allowlisted repositories, unique across the whole list."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from pathlib import PurePath
from typing import Self, final

__all__ = ["RepositoryNames"]


@final
class RepositoryNames:
    """Each repository's last path segment, with parents added until unique.

    Spoken, not typed: segments are joined by spaces, never slashes. A name
    stops growing at its root; the policy resolves and de-duplicates the
    allowlist, so two entries never share every segment.
    """

    __slots__ = ("_names",)
    _names: tuple[str, ...]

    def __new__(cls, projects: tuple[str, ...]) -> Self:
        self = super().__new__(cls)
        segments = tuple(cls._segments(PurePath(project)) for project in projects)
        depths = [1] * len(segments)
        names = cls._rendered(segments, depths)
        while (grown := cls._grown(segments, depths, names)) != depths:
            depths = grown
            names = cls._rendered(segments, depths)
        self._names = names
        return self

    def __len__(self) -> int:
        return len(self._names)

    def __iter__(self) -> Iterator[str]:
        return iter(self._names)

    def listed(self) -> str:
        """Return the names as one spoken list: "a", "a and b", "a, b and c".

        Raises:
            ValueError: If there are no names; an empty allowlist has its own
                spoken form, which the greeting chooses before calling this.
        """
        if not self._names:
            raise ValueError("no repositories to list")
        if len(self._names) == 1:
            return self._names[0]
        return f"{', '.join(self._names[:-1])} and {self._names[-1]}"

    @staticmethod
    def _segments(path: PurePath) -> tuple[str, ...]:
        """Return the spoken segments of a path, without its root."""
        return tuple(part for part in path.parts if part != path.anchor) or (str(path),)

    @staticmethod
    def _rendered(
        segments: tuple[tuple[str, ...], ...], depths: list[int]
    ) -> tuple[str, ...]:
        """Render each path from its last ``depth`` segments."""
        return tuple(
            " ".join(parts[-depth:])
            for parts, depth in zip(segments, depths, strict=True)
        )

    @staticmethod
    def _grown(
        segments: tuple[tuple[str, ...], ...],
        depths: list[int],
        names: tuple[str, ...],
    ) -> list[int]:
        """Deepen every name another path still shares, while it has a parent."""
        shared = Counter(names)
        return [
            depth + 1 if shared[name] > 1 and depth < len(parts) else depth
            for parts, depth, name in zip(segments, depths, names, strict=True)
        ]
