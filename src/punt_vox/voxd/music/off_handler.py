"""Music-off handler -- parse wire message, delegate to scheduler.turn_off."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Self

if TYPE_CHECKING:
    from starlette.websockets import WebSocket

from punt_vox.voxd.music.scheduler import MusicScheduler
from punt_vox.voxd.types import MessageHandler

__all__ = ["MusicOffHandler"]

logger = logging.getLogger(__name__)


class MusicOffHandler(MessageHandler):
    """Handle 'music_off' messages: stop music playback."""

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
        """Parse wire message and delegate to scheduler.turn_off."""
        request_id = str(msg.get("id", ""))

        response = await self._scheduler.turn_off()

        await websocket.send_json(
            {
                "type": "music_off",
                "id": request_id,
                "status": response.status,
            }
        )
