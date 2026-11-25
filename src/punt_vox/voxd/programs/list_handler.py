"""The ``program_list`` wire handler -- the album catalogue surface."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.programs.album_list_reader import AlbumListReader

if TYPE_CHECKING:
    from starlette.websockets import WebSocket

    from punt_vox.voxd.programs.service import ProgramService

__all__ = ["ListHandler"]


@final
class ListHandler:
    """Handle ``program_list``: reply with each catalog album's tags and counts."""

    __slots__ = ("_reader", "_service")
    _reader: AlbumListReader
    _service: ProgramService

    def __new__(cls, service: ProgramService) -> Self:
        """Create a list handler."""
        self = super().__new__(cls)
        self._reader = AlbumListReader()
        self._service = service
        return self

    async def __call__(self, msg: dict[str, object], websocket: WebSocket) -> None:
        """Read manifests on a worker, then reconcile removals on the loop."""
        rows = await self._reader.read(self._service)
        reply = {"type": "program_list", "id": str(msg.get("id", "")), "programs": rows}
        await websocket.send_json(reply)
