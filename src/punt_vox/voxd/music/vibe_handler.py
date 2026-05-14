"""Music-vibe handler -- parse wire message, delegate to scheduler.update_vibe."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Self

if TYPE_CHECKING:
    from starlette.websockets import WebSocket

from punt_vox.voxd.music.scheduler import MusicScheduler
from punt_vox.voxd.types import MessageHandler

__all__ = ["MusicVibeHandler"]

logger = logging.getLogger(__name__)


class MusicVibeHandler(MessageHandler):
    """Handle 'music_vibe' messages: update vibe if sender is owner."""

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
        """Parse wire message and delegate to scheduler.update_vibe."""
        request_id = str(msg.get("id", ""))
        owner_id = str(msg.get("owner_id", ""))
        vibe = str(msg.get("vibe", ""))
        vibe_tags = str(msg.get("vibe_tags", ""))

        try:
            response = self._scheduler.update_vibe(owner_id, (vibe, vibe_tags))
        except ValueError as exc:
            await websocket.send_json(
                {"type": "error", "id": request_id, "message": str(exc)}
            )
            return

        await websocket.send_json(
            {
                "type": "music_vibe",
                "id": request_id,
                "status": response.status,
            }
        )
