"""Handle WebSocket requests to synthesize speech for playback."""

from __future__ import annotations

import asyncio
import logging
from contextlib import ExitStack
from typing import TYPE_CHECKING, Self

from punt_vox.voxd._parse import parse_optional_int
from punt_vox.voxd.dedup import OnceDedup
from punt_vox.voxd.playback import PlaybackItem, PlaybackQueue
from punt_vox.voxd.speech_reply import SpeechReply
from punt_vox.voxd.speech_request import SpeechRequest
from punt_vox.voxd.synthesis import (
    _LOCAL_PROVIDERS,  # pyright: ignore[reportPrivateUsage]
    SynthesisPipeline,
)
from punt_vox.voxd.types import MessageHandler

if TYPE_CHECKING:
    from starlette.websockets import WebSocket

__all__ = ["SynthesizeHandler"]

logger = logging.getLogger(__name__)


class SynthesizeHandler(MessageHandler):
    """Handle 'synthesize' WebSocket messages: TTS + enqueue playback."""

    __slots__ = ("_once_dedup", "_playback", "_synthesis")

    _once_dedup: OnceDedup
    _playback: PlaybackQueue
    _synthesis: SynthesisPipeline

    def __new__(
        cls,
        *,
        synthesis: SynthesisPipeline,
        playback: PlaybackQueue,
        once_dedup: OnceDedup,
    ) -> Self:
        """Create a synthesize handler."""
        self = super().__new__(cls)
        self._synthesis = synthesis
        self._playback = playback
        self._once_dedup = once_dedup
        return self

    async def __call__(self, msg: dict[str, object], websocket: WebSocket) -> None:
        """Synthesize speech and enqueue for playback."""
        # Malformed input gets an id-stamped reply without closing the connection.
        try:
            req = SpeechRequest.from_msg(msg, websocket)
            once = parse_optional_int(msg, "once") or 0
        except ValueError as exc:
            # WireReply makes a gone-peer send a clean no-op, matching the siblings.
            await SpeechReply(websocket, str(msg.get("id", ""))).error(exc)
            return

        await self._dispatch(req, once)

    async def _dispatch(self, req: SpeechRequest, once: int) -> None:
        """Play a local provider directly, else synthesize to a file and enqueue."""
        with ExitStack() as reservation:
            if once > 0:
                hit = self._once_dedup.check_and_record(req.text, float(once))
                if hit is not None:
                    await req.reply_duplicate(hit)
                    return
                reservation.callback(self._once_dedup.rollback_for(req.text))

            logger.info("Synthesize: id=%r chars=%d", req.request_id, len(req.text))
            if await self._play_local(req, reservation):
                return
            await self._synthesize_and_enqueue(req, reservation)

    async def _play_local(self, req: SpeechRequest, reservation: ExitStack) -> bool:
        """Play a local provider (espeak/say) straight to the device.

        Return True when the request was fully handled (a terminal reply was
        sent); False when no local path applied and the caller should fall
        through to file synthesis.
        """
        if req.spec.provider not in _LOCAL_PROVIDERS:
            return False
        result = await self._synthesis.try_direct_play(
            req.text, req.spec, record_result=self._playback.record_result
        )
        if result is None:
            return False
        if result == 0:
            reservation.pop_all()
            await req.reply({"type": "done"})
        else:
            reservation.close()
            await req.fail(result)
        return True

    async def _synthesize_and_enqueue(
        self, req: SpeechRequest, reservation: ExitStack
    ) -> None:
        """Synthesize to a file, enqueue it, and drive the playing/done replies."""
        outcome = await req.synthesize(self._synthesis, on_failure=reservation.close)
        if outcome is None:
            return

        # `cached` rides the 'playing' response (the client's terminal).
        item = PlaybackItem(
            path=outcome.path,
            request_id=req.request_id,
            notify=asyncio.Event(),
            discard=outcome.discard,
            on_failure=reservation.pop_all().close,
        )
        try:
            await self._playback.enqueue(item)
        except BaseException:
            item.finish("playback was not queued")
            raise
        await req.reply({"type": "playing", "cached": outcome.cached})
        await item.notify.wait()
        await req.reply({"type": "done"})
