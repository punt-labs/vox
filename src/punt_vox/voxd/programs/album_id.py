"""The short unique hex identity every album carries (``AlbumId``).

An ``AlbumId`` is the album's stable handle: the directory suffix (``<slug>-<id>``)
and the catalog key. The id value-space is this type's, so the collision-avoiding
mint loop lives here and :meth:`Catalog.mint_id` delegates to it.
Construction validates the hex shape, so a wire- or filesystem-derived id can
never smuggle a path separator or non-hex junk into a directory name.
"""

from __future__ import annotations

import secrets
from collections.abc import Container
from typing import ClassVar, Final, Self, final

from punt_vox.voxd.programs.hex_token import HexToken

__all__ = ["AlbumId"]

_ID_BYTES: Final = 3  # six hex chars -- 16.7M ids, ample for a personal library


@final
class AlbumId(HexToken):
    """A short, unique, lowercase-hex album identity (``secrets.token_hex(3)``).

    Validation, the ``value`` accessor, and the value-object dunders come from
    :class:`HexToken`; this subclass adds only the collision-avoiding mint factory,
    so :meth:`Catalog.mint_id` delegates to it.
    """

    __slots__ = ()
    _LABEL: ClassVar[str] = "album id"

    @classmethod
    def mint(cls, taken: Container[AlbumId]) -> Self:
        """Return a fresh id absent from ``taken`` (owns the collision-retry loop)."""
        while True:
            candidate = cls(secrets.token_hex(_ID_BYTES))
            if candidate not in taken:
                return candidate
