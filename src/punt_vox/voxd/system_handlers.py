"""System-level WebSocket handlers: chime, voices, health."""
# pyright: reportPrivateUsage=false
# Internal module within the voxd package -- cross-module private access is expected.

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Self

from starlette.websockets import WebSocket, WebSocketDisconnect

from punt_vox.providers import auto_detect_provider, get_provider
from punt_vox.voxd._parse import parse_optional_str
from punt_vox.voxd.chimes import ChimeResolver
from punt_vox.voxd.dedup import ChimeDedup
from punt_vox.voxd.health import DaemonHealth
from punt_vox.voxd.playback import PlaybackItem, PlaybackQueue

__all__ = ["SystemHandlers"]

logger = logging.getLogger(__name__)


class SystemHandlers:
    """Handle chime, voices, and health WebSocket messages."""

    __slots__ = (
        "_chime_dedup",
        "_chimes",
        "_health",
        "_playback",
    )

    _chime_dedup: ChimeDedup
    _chimes: ChimeResolver
    _health: DaemonHealth
    _playback: PlaybackQueue

    def __new__(
        cls,
        *,
        chimes: ChimeResolver,
        chime_dedup: ChimeDedup,
        playback: PlaybackQueue,
        health: DaemonHealth,
    ) -> Self:
        self = super().__new__(cls)
        self._chimes = chimes
        self._chime_dedup = chime_dedup
        self._playback = playback
        self._health = health
        return self

    async def handle_chime(
        self,
        msg: dict[str, object],
        websocket: WebSocket,
    ) -> None:
        """Handle a 'chime' message: play a bundled chime sound."""
        signal = str(msg.get("signal", "done"))
        path = self._chimes.resolve(signal)
        if path is None:
            logger.warning("Unknown chime signal: %s", signal)
            await websocket.send_json(
                {"type": "error", "id": "", "message": f"unknown chime: {signal}"}
            )
            return

        if not self._chime_dedup.should_play(signal):
            logger.info("Dedup: skipping duplicate chime %s", signal)
            await websocket.send_json({"type": "done", "id": ""})
            return

        logger.info("Chime: %s", signal)
        done_event = asyncio.Event()
        await self._playback.enqueue(
            PlaybackItem(path=path, request_id=f"chime:{signal}", notify=done_event)
        )
        await websocket.send_json({"type": "playing", "id": f"chime:{signal}"})
        await done_event.wait()
        with contextlib.suppress(WebSocketDisconnect, RuntimeError):
            await websocket.send_json({"type": "done", "id": f"chime:{signal}"})

    async def handle_voices(
        self,
        msg: dict[str, object],
        websocket: WebSocket,
    ) -> None:
        """Handle a 'voices' message: list available voices."""
        provider_name = parse_optional_str(msg, "provider") or auto_detect_provider()

        try:
            provider = get_provider(provider_name, config_dir=None)
            voice_list = await asyncio.to_thread(provider.list_voices)
        except Exception as exc:
            logger.exception("Voice listing failed for provider=%s", provider_name)
            await websocket.send_json(
                {
                    "type": "error",
                    "id": "",
                    "message": f"voice listing failed: {exc}",
                }
            )
            return

        await websocket.send_json(
            {"type": "voices", "provider": provider_name, "voices": voice_list}
        )

    async def handle_health(
        self,
        _msg: dict[str, object],
        websocket: WebSocket,
    ) -> None:
        """Handle a 'health' message over the authenticated WebSocket."""
        payload = self._health.full_payload()
        payload["type"] = "health"
        await websocket.send_json(payload)
