"""Serve a sorted recordings snapshot as bounded pages on one WebSocket."""

from __future__ import annotations

import asyncio
from operator import attrgetter
from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.wire_fault import SafeFault
from punt_vox.voxd.wire_reply import WireReply

if TYPE_CHECKING:
    from starlette.websockets import WebSocket

    from punt_vox.voxd.record_store import RecordingEntry, RecordStore

__all__ = ["RecListHandler"]

_PAGE_ROWS = 128


@final
class RecListHandler:
    """Scan once per request, then send consecutive pages in name order."""

    __slots__ = ("_store",)
    _store: RecordStore

    def __new__(cls, store: RecordStore) -> Self:
        """Bind the daemon's recording store."""
        self = super().__new__(cls)
        self._store = store
        return self

    async def __call__(self, msg: dict[str, object], websocket: WebSocket) -> None:
        """Send the listing pages or an audited fault."""
        request_id = str(msg.get("id", ""))
        reply = WireReply(websocket, request_id)
        try:
            entries = await self._snapshot()
        except OSError as exc:
            await reply.fault(SafeFault.from_exception(exc))
            return
        await self._stream(reply, entries)

    async def _snapshot(self) -> list[RecordingEntry]:
        entries: list[RecordingEntry] = []
        batches = self._store.entry_batches()
        while (batch := await asyncio.to_thread(next, batches, None)) is not None:
            entries.extend(batch)
        await asyncio.to_thread(entries.sort, key=attrgetter("name"))
        return entries

    @classmethod
    async def _stream(cls, reply: WireReply, entries: list[RecordingEntry]) -> None:
        for page, start in enumerate(range(0, len(entries) or 1, _PAGE_ROWS)):
            end = start + _PAGE_ROWS
            more = end < len(entries)
            if not await reply.send(
                {
                    "type": "recordings_page",
                    "page": page,
                    "more": more,
                    "entries": cls._rows(entries[start:end]),
                }
            ):
                return
            if more:
                await asyncio.sleep(0)

    @staticmethod
    def _rows(entries: list[RecordingEntry]) -> list[dict[str, object]]:
        return [{"name": entry.name, "bytes": entry.byte_count} for entry in entries]
