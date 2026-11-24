"""Decode paged recordings replies."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, final

from punt_vox.client_errors import VoxdProtocolError
from punt_vox.types_programs import JsonObject

if TYPE_CHECKING:
    from punt_vox.client_exchange import VoxdExchange

__all__ = ["RecordingListing", "RecordingSummary"]


@dataclass(frozen=True, slots=True)
class RecordingSummary:
    """One recording in the store's list: its bare name and byte count."""

    name: str
    byte_count: int


@final
class RecordingListing:
    """Collect a complete listing under one deadline; never return a partial list."""

    @staticmethod
    async def read(
        exchange: VoxdExchange, *, timeout: float
    ) -> tuple[RecordingSummary, ...]:
        """Read every page before the shared deadline."""
        deadline = asyncio.get_running_loop().time() + timeout
        rows: list[RecordingSummary] = []
        page = 0
        while True:
            frame = await exchange.recv_before(deadline, "recordings_page")
            obj = JsonObject.coerce(frame, "rec_list")
            if obj.require_str("type") != "recordings_page" or (
                obj.require_int("page") != page
            ):
                raise VoxdProtocolError(f"expected recordings page {page}")
            rows.extend(map(RecordingListing._row, obj.require_list("entries")))
            if not obj.require_bool("more"):
                return tuple(rows)
            page += 1

    @staticmethod
    def _row(item: object) -> RecordingSummary:
        row = JsonObject.coerce(item, "rec_list.entries")
        return RecordingSummary(
            name=row.require_str("name"), byte_count=row.require_int("bytes")
        )
