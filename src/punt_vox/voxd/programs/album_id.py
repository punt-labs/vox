"""The short unique hex identity every album carries (``AlbumId``).

An ``AlbumId`` is the album's stable handle: the directory suffix (``<slug>-<id>``)
and the catalog key. The id value-space is this type's, so the collision-avoiding
mint loop lives here (finding #8) and :meth:`Catalog.mint_id` delegates to it.
Construction validates the hex shape, so a wire- or filesystem-derived id can
never smuggle a path separator or non-hex junk into a directory name.
"""

from __future__ import annotations

import secrets
from collections.abc import Container
from typing import Final, Self, final

__all__ = ["AlbumId"]

_ID_BYTES: Final = 3  # six hex chars -- 16.7M ids, ample for a personal library
_HEX_DIGITS: Final = frozenset("0123456789abcdef")


@final
class AlbumId:
    """A short, unique, lowercase-hex album identity (``secrets.token_hex(3)``)."""

    __slots__ = ("_value",)
    _value: str

    def __new__(cls, value: str) -> Self:
        cleaned = value.strip()
        if not cleaned:
            msg = "album id must be non-empty"
            raise ValueError(msg)
        if not all(char in _HEX_DIGITS for char in cleaned):
            msg = f"album id must be lowercase hex: {value!r}"
            raise ValueError(msg)
        self = super().__new__(cls)
        self._value = cleaned
        return self

    @classmethod
    def mint(cls, taken: Container[AlbumId]) -> Self:
        """Return a fresh id absent from ``taken`` (owns the collision loop, #8)."""
        while True:
            candidate = cls(secrets.token_hex(_ID_BYTES))
            if candidate not in taken:
                return candidate

    @property
    def value(self) -> str:
        """Return the hex id string."""
        return self._value

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, AlbumId):
            return NotImplemented
        return self._value == other._value

    def __hash__(self) -> int:
        return hash((AlbumId, self._value))

    def __repr__(self) -> str:
        return f"AlbumId({self._value!r})"

    def __str__(self) -> str:
        return self._value
