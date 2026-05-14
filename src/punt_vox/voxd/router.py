"""WebSocket message routing for voxd."""
# pyright: reportPrivateUsage=false
# Internal module within the voxd package -- cross-module private access is expected.

from __future__ import annotations

import hmac
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Self

from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from punt_vox.voxd.music_handlers import MusicHandlers
from punt_vox.voxd.speech_handlers import SpeechHandlers
from punt_vox.voxd.system_handlers import SystemHandlers

__all__ = ["WebSocketRouter"]

logger = logging.getLogger(__name__)


class WebSocketRouter:
    """Route WebSocket messages to handler methods for voxd."""

    __slots__ = (
        "_auth_token",
        "_client_count",
        "_handlers",
        "_music_handlers",
        "_speech_handlers",
        "_system_handlers",
    )

    _auth_token: str | None
    _client_count: int
    _handlers: dict[str, Callable[[dict[str, object], WebSocket], Awaitable[None]]]
    _music_handlers: MusicHandlers
    _speech_handlers: SpeechHandlers
    _system_handlers: SystemHandlers

    def __new__(
        cls,
        *,
        speech_handlers: SpeechHandlers,
        music_handlers: MusicHandlers,
        system_handlers: SystemHandlers,
        auth_token: str | None,
    ) -> Self:
        self = super().__new__(cls)
        self._speech_handlers = speech_handlers
        self._music_handlers = music_handlers
        self._system_handlers = system_handlers
        self._auth_token = auth_token
        self._client_count = 0
        self._handlers = {
            "synthesize": speech_handlers.handle_synthesize,
            "record": speech_handlers.handle_record,
            "chime": system_handlers.handle_chime,
            "voices": system_handlers.handle_voices,
            "health": system_handlers.handle_health,
            "music_on": music_handlers.handle_music_on,
            "music_off": music_handlers.handle_music_off,
            "music_play": music_handlers.handle_music_play,
            "music_list": music_handlers.handle_music_list,
            "music_vibe": music_handlers.handle_music_vibe,
            "music_next": music_handlers.handle_music_next,
        }
        return self

    # -- Properties ------------------------------------------------------------

    @property
    def client_count(self) -> int:
        """Return the number of connected WebSocket clients."""
        return self._client_count

    @property
    def handlers(
        self,
    ) -> dict[str, Callable[[dict[str, object], WebSocket], Awaitable[None]]]:
        """Return the handler dispatch table."""
        return self._handlers

    # -- Connection handler ----------------------------------------------------

    async def handle_connection(self, websocket: WebSocket) -> None:
        """Main WebSocket route at /ws."""
        if not self._check_auth(websocket):
            await websocket.close(code=1008)
            return

        await websocket.accept()
        self._client_count += 1
        logger.info("Client connected (total: %d)", self._client_count)

        try:
            while True:
                # Preempt Starlette's RuntimeError on a peer-closed socket.
                # After the vox-ehf fix in 4.3.0, chime/unmute clients return
                # on the "playing" ack and close the WebSocket while this
                # loop is still awaiting the next receive_text(). See vox-ewh.
                if websocket.application_state != WebSocketState.CONNECTED:
                    break
                raw = await websocket.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    await websocket.send_json(
                        {"type": "error", "id": "", "message": "invalid JSON"}
                    )
                    continue

                if not isinstance(msg, dict):
                    await websocket.send_json(
                        {"type": "error", "id": "", "message": "expected JSON object"}
                    )
                    continue

                msg_type = str(msg.get("type", ""))  # pyright: ignore[reportUnknownMemberType,reportUnknownArgumentType]
                handler = self._handlers.get(msg_type)
                if handler is None:
                    msg_id = str(msg.get("id", ""))  # pyright: ignore[reportUnknownMemberType,reportUnknownArgumentType]
                    await websocket.send_json(
                        {
                            "type": "error",
                            "id": msg_id,
                            "message": f"unknown message type: {msg_type}",
                        }
                    )
                    continue

                await handler(msg, websocket)  # pyright: ignore[reportUnknownArgumentType]
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("WebSocket error")
        finally:
            self._client_count -= 1
            logger.info("Client disconnected (total: %d)", self._client_count)

    # -- Auth ------------------------------------------------------------------

    def _check_auth(self, websocket: WebSocket) -> bool:
        """Verify the auth token from query param."""
        if self._auth_token is None:
            return True  # No auth configured (tests)
        token = websocket.query_params.get("token", "")
        return hmac.compare_digest(token, self._auth_token)
