"""Errors reported by the album store."""

from __future__ import annotations

from typing import final

__all__ = ["AlbumNotFoundError"]


@final
class AlbumNotFoundError(LookupError):
    """The catalogued album no longer has a manifest in the store."""

    __slots__ = ()
