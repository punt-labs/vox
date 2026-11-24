"""Read an export manifest without blocking the catalog's event loop."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, final

from punt_vox.voxd.programs.album_contents import AlbumContents
from punt_vox.voxd.programs.store_errors import AlbumNotFoundError

if TYPE_CHECKING:
    from collections.abc import Callable

    from punt_vox.voxd.containment import ContainmentRoot
    from punt_vox.voxd.programs.album_id import AlbumId
    from punt_vox.voxd.programs.catalog import Album

__all__ = ["AlbumExport"]


@final
class AlbumExport:
    """Coordinate the live disk read with the event-loop-owned catalog."""

    __slots__ = ()

    @staticmethod
    async def read(
        album: Album, root: ContainmentRoot, require_album: Callable[[AlbumId], Album]
    ) -> AlbumContents:
        """Read off-loop, then reject an album removed while the worker ran.

        A vanished manifest for an album still in the catalog remains a fault.
        """
        try:
            contents = await asyncio.to_thread(AlbumContents.from_album, album, root)
        except AlbumNotFoundError:
            require_album(album.id)
            raise
        require_album(album.id)
        return contents
