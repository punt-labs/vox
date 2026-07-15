"""The bounded, tag-safe label a session mood collapses to for pool metadata."""

from __future__ import annotations

import re
from typing import Final, Self, final

__all__ = ["VibeLabel"]

_MAX_LABEL_CHARS: Final = 48
_SAFE_PUNCTUATION: Final = frozenset("-.,'")
_NON_SLUG: Final = re.compile(r"[^a-z0-9]+")


@final
class VibeLabel:
    """A session mood bounded to a short, tag-safe metadata label.

    The auto-vibe mood is a whole session narrative -- right for colouring a few
    seconds of speech, wrong for a persisted pool tag or an ID3 frame. This value
    object is the one seam that bounds it: control characters and tag-hostile
    punctuation collapse to spaces, interior whitespace collapses to one space,
    and the result is capped to a short label -- at a word boundary when the cap
    lands near one, otherwise a hard cut so a long single word is bounded, never
    dropped. A mood with no alphanumeric content normalizes to the empty label --
    empty is a valid vibe, junk prose is not.
    """

    __slots__ = ("_value",)
    _value: str

    def __new__(cls, raw: str) -> Self:
        self = super().__new__(cls)
        self._value = cls._bound(raw)
        return self

    @property
    def value(self) -> str:
        """Return the bounded, tag-safe label (``""`` when nothing is usable)."""
        return self._value

    def name_segment(self, limit: int) -> str:
        """Return the label as a length-capped slug segment (``""`` when empty).

        Lowercases and hyphen-slugs the bounded value, then clips it to ``limit``
        -- at a hyphen boundary when the cap lands near one, otherwise a hard cut
        so a single long token is bounded, not dropped. The safe, non-null
        building block for a pool's auto-name, which must never balloon into a
        many-word slug.
        """
        slug = _NON_SLUG.sub("-", self._value.lower()).strip("-")
        return self._clip(slug, limit, "-")

    def __bool__(self) -> bool:
        """Return whether the label carries any usable text."""
        return bool(self._value)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, VibeLabel):
            return NotImplemented
        return self._value == other._value

    def __hash__(self) -> int:
        return hash((VibeLabel, self._value))

    def __repr__(self) -> str:
        return f"VibeLabel({self._value!r})"

    def __str__(self) -> str:
        return self._value

    @classmethod
    def _bound(cls, raw: str) -> str:
        """Return ``raw`` collapsed to a short, tag-safe label, or ``""``."""
        kept = "".join(
            char if (char.isalnum() or char in _SAFE_PUNCTUATION) else " "
            for char in raw
        )
        collapsed = " ".join(kept.split())
        if not any(char.isalnum() for char in collapsed):
            return ""
        return cls._clip(collapsed, _MAX_LABEL_CHARS, " ")

    @staticmethod
    def _clip(text: str, limit: int, sep: str) -> str:
        """Return ``text`` bounded to ``limit`` chars, cut on a ``sep`` when one fits.

        Prefer the last ``sep`` within the cap; with none, hard-cap at ``limit``
        so a separator-less token is bounded, never dropped. ``len <= limit`` is
        the contract; the boundary cut is a nicety. A non-positive ``limit``
        yields ``""``.
        """
        if limit <= 0:  # non-positive cap has no room; guard the negative index below
            return ""
        if len(text) <= limit:
            return text
        # Search one past the cap so a ``sep`` exactly on ``limit`` keeps the whole
        # prefix; ``> 0`` rejects a leading ``sep`` and the -1 miss (both hard-cap).
        boundary = text[: limit + 1].rfind(sep)
        return text[:boundary] if boundary > 0 else text[:limit]
