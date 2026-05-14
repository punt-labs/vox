"""Music-play handler -- parse wire message, delegate to scheduler.play_track."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Self

if TYPE_CHECKING:
    from starlette.websockets import WebSocket

from punt_vox.voxd.music.scheduler import MusicScheduler
from punt_vox.voxd.types import MessageHandler

__all__ = ["MusicPlayHandler"]

logger = logging.getLogger(__name__)


class MusicPlayHandler(MessageHandler):
    """Handle 'music_play' messages: replay a saved track by name."""

    __slots__ = ("_scheduler",)

    _scheduler: MusicScheduler

    def __new__(cls, *, scheduler: MusicScheduler) -> Self:
        self = super().__new__(cls)
        self._scheduler = scheduler
        return self

    async def __call__(
        self,
        msg: dict[str, object],
        websocket: WebSocket,
    ) -> None:
        """Parse wire message and delegate to scheduler.play_track."""
        request_id = str(msg.get("id", ""))
        name = str(msg.get("name", ""))
        owner_id = str(msg.get("owner_id", ""))

        try:
            response = await self._scheduler.play_track(name, owner_id)
        except ValueError as exc:
            await websocket.send_json(
                {"type": "error", "id": request_id, "message": str(exc)}
            )
            return

        payload: dict[str, object] = {
            "type": "music_play",
            "id": request_id,
            "status": response.status,
        }
        if response.track is not None:
            payload["track"] = response.track
        if response.name is not None:
            payload["name"] = response.name
        await websocket.send_json(payload)
