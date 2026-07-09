"""The ordered ``Selection`` of ready Parts and the album-tagged ``SelectedPart``.

A ``Selection`` is the consume-only replay target: an ordered tuple of ready
Parts drawn from one or more albums (the Z ``Selection == power PART``, refined
to an ordered tuple for deterministic rotation). Each :class:`SelectedPart` carries an
*opaque locator* -- the album's directory-name string, never a live ``Path``, so
this module imports no ``pathlib`` (finding #3); the store dereferences the
locator to a directory. A union of two albums whose files both count ``001.mp3``
stays distinct because each part's :attr:`SelectedPart.playable` identity is
namespaced by its locator -- faithful to the Z Selection as a set of distinct
Parts.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Self, final

from punt_vox.voxd.programs.part import Part

__all__ = ["SelectedPart", "Selection"]


@final
@dataclass(frozen=True, slots=True)
class SelectedPart:
    """One ready Part drawn from an album, tagged with its opaque locator.

    ``locator`` is the album's directory-name string (the persistence seam
    resolves it to a ``Path``). :attr:`playable` is a selection-unique Part whose
    identity is namespaced ``<locator>/<file>`` so two albums that both hold
    ``001.mp3`` contribute distinct members to a union -- the refinement that
    realises the Z Selection's set-of-distinct-Parts semantics.
    """

    part: Part
    locator: str

    @property
    def playable(self) -> Part:
        """Return a selection-unique Part (``<locator>/<file>``) for rotation."""
        return Part(f"{self.locator}/{self.part.identity}", self.part.index)


@final
class Selection:
    """An ordered tuple of album-tagged ready Parts -- one or more albums' union."""

    __slots__ = ("_parts", "_pool")
    _parts: tuple[SelectedPart, ...]
    _pool: tuple[Part, ...]

    def __new__(cls, parts: tuple[SelectedPart, ...]) -> Self:
        self = super().__new__(cls)
        self._parts = parts
        # The rotation pool is a pure map over the immutable parts, so it never
        # changes -- compute it once here rather than rebuilding an O(n) tuple on
        # every rotate over a large cross-library selection.
        self._pool = tuple(selected.playable for selected in parts)
        return self

    @classmethod
    def from_albums(cls, albums: Iterable[tuple[str, Sequence[Part]]]) -> Self:
        """Build a Selection from ``(locator, ready_parts)`` pairs, in order."""
        parts = tuple(
            SelectedPart(part, locator) for locator, ready in albums for part in ready
        )
        return cls(parts)

    @property
    def parts(self) -> tuple[SelectedPart, ...]:
        """Return the selected parts in order."""
        return self._parts

    def playable_pool(self) -> tuple[Part, ...]:
        """Return the cached selection-unique playable Parts (the rotation pool)."""
        return self._pool

    def __len__(self) -> int:
        return len(self._parts)

    def __bool__(self) -> bool:
        return bool(self._parts)

    def __iter__(self) -> Iterator[SelectedPart]:
        return iter(self._parts)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Selection):
            return NotImplemented
        return self._parts == other._parts

    def __hash__(self) -> int:
        return hash((Selection, self._parts))

    def __repr__(self) -> str:
        return f"Selection(parts={len(self._parts)})"
