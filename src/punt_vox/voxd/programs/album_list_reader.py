"""Read live album rows without holding up the daemon's event loop."""

from __future__ import annotations

import asyncio
import logging
from threading import Event
from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.programs.types import AlbumListRow, ListRead

if TYPE_CHECKING:
    from punt_vox.voxd.programs.catalog import Album, Catalog
    from punt_vox.voxd.programs.service import ProgramService

__all__ = ["AlbumListReader"]

logger = logging.getLogger(__name__)


@final
class AlbumListReader:
    """Give catalog listings one worker and stop cancelled scans between albums."""

    __slots__ = ("_slot",)
    _slot: asyncio.Lock

    def __new__(cls) -> Self:
        """Create a reader shared by requests to one daemon."""
        self = super().__new__(cls)
        self._slot = asyncio.Lock()
        return self

    async def read(self, service: ProgramService) -> list[AlbumListRow]:
        """Snapshot and reconcile the catalog on its loop; read its rows off-loop."""
        await self._slot.acquire()
        stopped = Event()
        try:
            work = asyncio.get_running_loop().run_in_executor(
                None, self._read_snapshot, service.catalog_albums(), stopped
            )
        except BaseException:
            self._slot.release()
            raise
        work.add_done_callback(self._release_slot)
        try:
            reads = await asyncio.shield(work)
        except asyncio.CancelledError:
            stopped.set()
            work.add_done_callback(self._report_abandoned_failure)
            raise
        return self._current_rows(service.catalog, reads)

    def _release_slot(self, _: asyncio.Future[list[ListRead]]) -> None:
        self._slot.release()

    @staticmethod
    def _report_abandoned_failure(work: asyncio.Future[list[ListRead]]) -> None:
        if not work.cancelled() and (error := work.exception()) is not None:
            logger.error("Abandoned catalog read failed", exc_info=error)

    @classmethod
    def _read_snapshot(cls, albums: tuple[Album, ...], stop: Event) -> list[ListRead]:
        reads: list[ListRead] = []
        for album in albums:
            if stop.is_set():
                break
            reads.append((album, cls._read_or_error(album)))
        return reads

    @classmethod
    def _read_or_error(cls, album: Album) -> AlbumListRow | str:
        """Isolate a broken album so the rest of the listing survives."""
        try:
            return cls._row(album)
        except (LookupError, OSError, ValueError) as error:
            return f"{type(error).__name__}: {error}"

    @staticmethod
    def _row(album: Album) -> AlbumListRow:
        """Count Parts from the live manifest; fill can add them after startup."""
        manifest = album.read()
        tags = manifest.tags
        return AlbumListRow(
            id=manifest.id.value,
            format=manifest.format.label,
            style=tags.style,
            vibe=tags.vibe,
            name=tags.name,
            ready=len(manifest.ready_parts()),
            total=len(manifest.parts),
            created=manifest.created.isoformat(),
        )

    @staticmethod
    def _current_rows(catalog: Catalog, reads: list[ListRead]) -> list[AlbumListRow]:
        """Skip removed or replaced albums and report faults for current entries."""
        rows: list[AlbumListRow] = []
        for album, row in reads:
            if catalog.by_id(album.id) is not album:
                continue
            if isinstance(row, str):
                logger.error(
                    "skipping unreadable album %s at %s: %s",
                    album.id.value,
                    album.locator,
                    row,
                )
            else:
                rows.append(row)
        return rows
