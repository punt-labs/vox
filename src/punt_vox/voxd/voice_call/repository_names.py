"""Spoken names for the allowlisted repositories, unique across the whole list."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from typing import Self, final

from punt_vox.voxd.voice_call.spoken_path import SpokenPath

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
        paths = [SpokenPath(project) for project in projects]
        while cls._deepened(paths):
            pass
        self._names = tuple(path.spoken() for path in paths)
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
        *leading, last = self._names
        return f"{', '.join(leading)} and {last}" if leading else last

    @staticmethod
    def _deepened(paths: list[SpokenPath]) -> bool:
        """Deepen every path whose spoken name another still shares.

        Returns whether any name changed, so the caller loops until none does.
        """
        shared = Counter(path.spoken() for path in paths)
        deepened = [path.deepen() for path in paths if shared[path.spoken()] > 1]
        return any(deepened)
