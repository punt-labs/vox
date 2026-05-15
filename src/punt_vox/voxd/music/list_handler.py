"""Music-list handler -- parse wire message, delegate to generator.list_tracks."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self

if TYPE_CHECKING:
    from starlette.websockets import WebSocket

from punt_vox.voxd.music.generator import TrackGenerator
from punt_vox.voxd.types import MessageHandler

__all__ = ["MusicListHandler"]


class MusicListHandler(MessageHandler):
    """Handle 'music_list' messages: return saved tracks with metadata."""

    __slots__ = ("_generator",)

    _generator: TrackGenerator

    def __new__(cls, *, generator: TrackGenerator) -> Self:
        self = super().__new__(cls)
        self._generator = generator
        return self

    async def __call__(
        self,
        msg: dict[str, object],
        websocket: WebSocket,
    ) -> None:
        """Parse wire message and delegate to generator.list_tracks."""
        request_id = str(msg.get("id", ""))
        tracks = self._generator.list_tracks()

        await websocket.send_json(
            {
                "type": "music_list",
                "id": request_id,
                "tracks": [t.to_dict() for t in tracks],
            }
        )
