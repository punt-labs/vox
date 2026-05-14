"""Music-next handler -- parse wire message, delegate to scheduler.skip_next."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Self

if TYPE_CHECKING:
    from starlette.websockets import WebSocket

from punt_vox.voxd.music.scheduler import MusicScheduler
from punt_vox.voxd.types import MessageHandler

__all__ = ["MusicNextHandler"]

logger = logging.getLogger(__name__)


class MusicNextHandler(MessageHandler):
    """Handle 'music_next' messages: skip to a new track."""

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
        """Parse wire message and delegate to scheduler.skip_next."""
        request_id = str(msg.get("id", ""))
        owner_id = str(msg.get("owner_id", ""))

        try:
            response = self._scheduler.skip_next(owner_id)
        except ValueError as exc:
            await websocket.send_json(
                {"type": "error", "id": request_id, "message": str(exc)}
            )
            return

        await websocket.send_json(
            {
                "type": "music_next",
                "id": request_id,
                "status": response.status,
            }
        )
