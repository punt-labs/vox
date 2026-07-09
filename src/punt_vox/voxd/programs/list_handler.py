"""The ``program_list`` wire handler -- the album catalogue surface."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Self, final

if TYPE_CHECKING:
    from starlette.websockets import WebSocket

    from punt_vox.voxd.programs.catalog import Album
    from punt_vox.voxd.programs.service import ProgramService

__all__ = ["ListHandler"]

logger = logging.getLogger(__name__)


@final
class ListHandler:
    """Handle ``program_list``: reply with each catalog album's tags and counts."""

    __slots__ = ("_service",)
    _service: ProgramService

    def __new__(cls, service: ProgramService) -> Self:
        self = super().__new__(cls)
        self._service = service
        return self

    async def __call__(self, msg: dict[str, object], websocket: WebSocket) -> None:
        """Reply with each *readable* catalog album's id, tags, counts, timestamp."""
        rows = [
            row
            for album in self._service.catalog_albums()
            if (row := self._row_or_skip(album)) is not None
        ]
        await websocket.send_json(
            {
                "type": "program_list",
                "id": str(msg.get("id", "")),
                "programs": rows,
            }
        )

    @staticmethod
    def _row_or_skip(album: Album) -> dict[str, object] | None:
        """Return the album's row, or ``None`` to skip one gone unreadable.

        A row reads Parts live from disk, so an album whose directory was deleted
        or whose manifest became unreadable after startup raises. Isolate per
        album -- log at ERROR with its id and locator and drop it -- so a single
        broken entry never tears the socket down; the query returns the healthy
        rest. ``id`` and ``locator`` are durable metadata, safe to log.
        """
        try:
            return ListHandler._row(album)
        except (LookupError, OSError, ValueError) as exc:
            logger.error(
                "skipping unreadable album %s at %s: %s",
                album.id.value,
                album.locator,
                exc,
            )
            return None

    @staticmethod
    def _row(album: Album) -> dict[str, object]:
        """Return the catalogue row for one album (id, tags, counts, created).

        Part counts are read *live* from the store: the background fill grows the
        album after the catalog registers it, so a catalog-snapshot count would
        report ``0/0`` for a pool that has since filled. Metadata (id, tags,
        format, ``created``) is durable, so it comes from the live manifest too.
        """
        manifest = album.read()
        tags = manifest.tags
        return {
            "id": manifest.id.value,
            "format": manifest.format.label,
            "style": tags.style,
            "vibe": tags.vibe,
            "name": tags.name,
            "ready": len(manifest.ready_parts()),
            "total": len(manifest.parts),
            "created": manifest.created.isoformat(),
        }
