"""Decode paged recordings replies."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, final

from punt_vox.client_errors import VoxdProtocolError
from punt_vox.client_results import RecordingSummary
from punt_vox.types_programs import JsonObject

if TYPE_CHECKING:
    from punt_vox.client_exchange import VoxdExchange

__all__ = ["RecordingListing"]


@final
class RecordingListing:
    """Collect a complete listing, with a deadline for each page."""

    @staticmethod
    async def read(
        exchange: VoxdExchange, *, timeout: float
    ) -> tuple[RecordingSummary, ...]:
        """Bound the wait for the first page and for each subsequent page."""
        loop = asyncio.get_running_loop()
        rows: list[RecordingSummary] = []
        page = 0
        while True:
            frame = await exchange.recv_before(loop.time() + timeout, "recordings_page")
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
        return RecordingSummary.from_wire(JsonObject.coerce(item, "rec_list.entries"))
