"""The ``program_list`` wire handler -- the album catalogue surface."""

from __future__ import annotations

import asyncio
import logging
from itertools import batched
from typing import TYPE_CHECKING, Self, final

if TYPE_CHECKING:
    from starlette.websockets import WebSocket

    from punt_vox.voxd.programs.catalog import Album
    from punt_vox.voxd.programs.service import ProgramService

type _Read = tuple[Album, dict[str, object] | str]
_READ_BATCH_SIZE = 64


@final
class ListHandler:
    """Handle ``program_list``: reply with each catalog album's tags and counts."""

    __slots__ = ("_read_lock", "_service")
    _read_lock: asyncio.Lock
    _service: ProgramService

    def __new__(cls, service: ProgramService) -> Self:
        """Create a list handler."""
        self = super().__new__(cls)
        self._read_lock = asyncio.Lock()
        self._service = service
        return self

    async def __call__(self, msg: dict[str, object], websocket: WebSocket) -> None:
        """Read manifests on a worker, then reconcile removals on the loop."""
        rows = await self._read_rows(self._service.catalog_albums())
        reply = {"type": "program_list", "id": str(msg.get("id", "")), "programs": rows}
        await websocket.send_json(reply)

    async def _read_rows(self, albums: tuple[Album, ...]) -> list[dict[str, object]]:
        reads: list[_Read] = []
        # Share one worker among lists; cancellation stops the next batch.
        async with self._read_lock:
            for batch in batched(albums, _READ_BATCH_SIZE, strict=False):
                reads.extend(await asyncio.to_thread(list, map(self._read_row, batch)))
        catalog = self._service.catalog
        current = filter(lambda read: catalog.by_id(read[0].id) is read[0], reads)
        return list(filter(None, map(self._row_or_log, current)))

    @staticmethod
    def _read_row(album: Album) -> _Read:
        # The background fill can add parts after the catalog registers an album.
        try:
            manifest = album.read()
            return album, {
                "id": manifest.id.value,
                "format": manifest.format.label,
                "style": manifest.tags.style,
                "vibe": manifest.tags.vibe,
                "name": manifest.tags.name,
                "ready": len(manifest.ready_parts()),
                "total": len(manifest.parts),
                "created": manifest.created.isoformat(),
            }
        except (LookupError, OSError, ValueError) as exc:
            return album, f"{type(exc).__name__}: {exc}"

    @staticmethod
    def _row_or_log(read: _Read) -> dict[str, object] | None:
        album, row = read
        if not isinstance(row, str):
            return row
        logging.getLogger(__name__).error("Unreadable %r: %s", album, row)
        return None
