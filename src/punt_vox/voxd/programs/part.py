"""The Part value object, its lifecycle status, and the failed-Part map.

Realises the Z ``[PART]`` basic type together with the ``PartStatus`` free type
and the ``failedParts : PART \\pfun REASON`` finite map. The resolved surface
reference behind ``playlist:2`` (finding #7) lives in ``identifiers`` as
:class:`PartRef`.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from enum import StrEnum
from typing import Self

from punt_vox.types_programs.identifiers import Reason

__all__ = ["FrozenParts", "Part", "PartStatus"]


class PartStatus(StrEnum):
    """The lifecycle status of a stored Part (Z free type ``PartStatus``).

    Phase 1 ever writes only ``READY`` and ``FAILED`` (finding #9: atomic
    delivery hides ``PENDING``/``GENERATING``); the two in-flight statuses are
    declared now so the spoken formats promote them to stored per-Part state
    without a signature change.
    """

    PENDING = "pending"
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"


class Part:
    """A stored, playable unit of a Program (Z ``[PART]``) with its intrinsic index.

    ``identity`` is the opaque content-addressed identifier (the saved-audio
    file token); ``index`` is the 1-based manifest position assigned once at
    record time and never reused (MAJOR-1) -- the stable basis for ``playlist:2``
    addressing across daemon restarts and across processes. Identity is the
    ``identity`` alone: two Parts naming the same audio are equal, so a pool
    modelled as a ``frozenset[Part]`` dedups by audio, faithful to the Z ``pool``.
    """

    __slots__ = ("_identity", "_index")
    _identity: str
    _index: int

    def __new__(cls, identity: str, index: int) -> Self:
        if not identity:
            msg = "part identity must be non-empty"
            raise ValueError(msg)
        if index < 1:
            msg = f"part index must be >= 1, got {index}"
            raise ValueError(msg)
        self = super().__new__(cls)
        self._identity = identity
        self._index = index
        return self

    @property
    def identity(self) -> str:
        """Return the opaque content-addressed identifier."""
        return self._identity

    @property
    def index(self) -> int:
        """Return the 1-based intrinsic manifest index."""
        return self._index

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Part):
            return NotImplemented
        return self._identity == other._identity

    def __hash__(self) -> int:
        return hash((Part, self._identity))

    def __repr__(self) -> str:
        return f"Part(identity={self._identity!r}, index={self._index})"


class FrozenParts:
    """An immutable ``Part`` |-> ``Reason`` map -- a Program's permanently-failed Parts.

    Realises the Z ``failedParts`` finite partial function as a value object
    (PY-OO-4), not a raw ``dict``: immutable, hashable, and grown only through
    ``with_failure`` returning a fresh instance.
    """

    __slots__ = ("_by_part",)
    _by_part: dict[Part, Reason]

    def __new__(cls, mapping: Mapping[Part, Reason] | None = None) -> Self:
        self = super().__new__(cls)
        self._by_part = {} if mapping is None else dict(mapping)
        return self

    @classmethod
    def empty(cls) -> Self:
        """Return the empty failed-Part map (the Z ``\\emptyset``)."""
        return cls()

    def with_failure(self, part: Part, reason: Reason) -> FrozenParts:
        """Return a successor recording ``part`` as permanently failed."""
        updated = dict(self._by_part)
        updated[part] = reason
        return FrozenParts(updated)

    @property
    def parts(self) -> frozenset[Part]:
        """Return the failed Parts (the Z ``dom failedParts``)."""
        return frozenset(self._by_part)

    def reason_for(self, part: Part) -> Reason | None:
        """Return the retained reason for ``part``, or ``None`` if it did not fail."""
        return self._by_part.get(part)

    def ordered(self) -> tuple[tuple[Part, Reason], ...]:
        """Return the failures as ``(part, reason)`` pairs sorted by Part index."""
        return tuple(sorted(self._by_part.items(), key=lambda item: item[0].index))

    def __contains__(self, part: object) -> bool:
        return part in self._by_part

    def __len__(self) -> int:
        return len(self._by_part)

    def __iter__(self) -> Iterator[Part]:
        return iter(self._by_part)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, FrozenParts):
            return NotImplemented
        return self._by_part == other._by_part

    def __hash__(self) -> int:
        return hash((FrozenParts, frozenset(self._by_part.items())))

    def __repr__(self) -> str:
        return f"FrozenParts({self._by_part!r})"
