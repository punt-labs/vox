"""Music-on handler -- parse wire message, delegate to scheduler.turn_on."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Self

if TYPE_CHECKING:
    from starlette.websockets import WebSocket

from punt_vox.voxd.music.prompts import PromptSet
from punt_vox.voxd.music.scheduler import MusicScheduler
from punt_vox.voxd.types import MessageHandler

__all__ = ["MusicOnHandler"]

logger = logging.getLogger(__name__)


class MusicOnHandler(MessageHandler):
    """Handle 'music_on' messages: start or transfer music ownership."""

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
        """Parse wire message and delegate to scheduler.turn_on."""
        request_id = str(msg.get("id", ""))
        owner_id = str(msg.get("owner_id", ""))
        style = str(msg.get("style", ""))
        vibe = str(msg.get("vibe", ""))
        vibe_tags = str(msg.get("vibe_tags", ""))
        name = str(msg.get("name", ""))

        try:
            prompts = PromptSet.from_wire(msg)
            response = await self._scheduler.turn_on(
                owner_id, style, (vibe, vibe_tags), name, prompts=prompts
            )
        except ValueError as exc:
            await websocket.send_json(
                {"type": "error", "id": request_id, "message": str(exc)}
            )
            return

        payload: dict[str, object] = {
            "type": "music_on",
            "id": request_id,
            "status": response.status,
        }
        if response.track is not None:
            payload["track"] = response.track
        if response.name is not None:
            payload["name"] = response.name
        await websocket.send_json(payload)
