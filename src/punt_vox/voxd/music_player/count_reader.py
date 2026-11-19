"""Read ready-track counts from album manifests on disk.

TrackCountCache schedules this blocking work off event loops; CountStore decides
whether its generation may be committed.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.music_player.album_display import AlbumDisplay

if TYPE_CHECKING:
    from punt_vox.voxd.programs.album_id import AlbumId
    from punt_vox.voxd.programs.catalog import Album

__all__ = ["CountReader"]

logger = logging.getLogger(__name__)


@final
class CountReader:
    """Reads each album's live ready-track count from its on-disk manifest."""

    __slots__ = ()

    def __new__(cls) -> Self:
        """Create a count reader."""
        return super().__new__(cls)

    def read(self, albums: tuple[Album, ...]) -> dict[AlbumId, int]:
        """Return a fresh count per album; blocking, one disk read each.

        An album the store no longer holds is dropped rather than counted:
        ``LookupError`` is the catalog's documented "this was deleted"
        contract. Every other fault propagates -- a permission blip or
        descriptor exhaustion must surface, not silently freeze one album's
        count at its last-known value with nothing to say why.

        Dropping a deleted album here is not what keeps it off the screen --
        the catalog is. Deletion removes an album from the catalog
        synchronously in the same call that removes it from disk
        (:meth:`~punt_vox.voxd.programs.library.Library.remove`), well
        before any render sees it again; this only keeps a refresh that
        raced that removal from failing over it.
        """
        counts: dict[AlbumId, int] = {}
        for album in albums:
            try:
                counts[album.id] = AlbumDisplay.read(album).tracks
            except LookupError:
                logger.debug(
                    "album %s is no longer on disk; dropping its cached count",
                    album.id,
                )
        return counts
