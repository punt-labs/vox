"""Speech synthesis and recording WebSocket handlers."""
# pyright: reportPrivateUsage=false
# Internal module within the voxd package -- cross-module private access is expected.

from __future__ import annotations

import asyncio
import logging
from contextlib import ExitStack
from dataclasses import dataclass
from typing import TYPE_CHECKING, Self

from punt_vox.providers.api_key import ApiKeyCheck
from punt_vox.types_synthesis import SynthesisSpec
from punt_vox.voxd._parse import (
    parse_optional_float,
    parse_optional_int,
    parse_optional_str,
    parse_required_str,
)
from punt_vox.voxd.dedup import OnceDedup
from punt_vox.voxd.playback import PlaybackItem, PlaybackQueue
from punt_vox.voxd.speech_reply import SpeechReply
from punt_vox.voxd.synthesis import (  # pyright: ignore[reportPrivateUsage]
    _LOCAL_PROVIDERS,
    _SYNTHESIS_REJECTIONS,
    EmptyNormalizedTextError,
    SynthesisPipeline,
)
from punt_vox.voxd.types import MessageHandler
from punt_vox.voxd.wire_fault import SafeFault

if TYPE_CHECKING:
    from collections.abc import Callable

    from starlette.websockets import WebSocket

    from punt_vox.voxd.dedup import DedupHit
    from punt_vox.voxd.synthesis_result import SynthesisOutcome

__all__ = ["SynthesizeHandler"]

logger = logging.getLogger(__name__)

# A per-request key is checked where it enters the daemon, before any provider
# puts it in a header; the message names the wire field, never the key.
_REQUEST_KEY = ApiKeyCheck("the request's api_key")


@dataclass(frozen=True, slots=True)
class _SpeechRequest:
    """Bundle parsed synthesis fields and their id-stamped reply channel."""

    text: str
    spec: SynthesisSpec
    request_id: str
    websocket: WebSocket

    @property
    def _wire(self) -> SpeechReply:
        return SpeechReply(self.websocket, self.request_id)

    @classmethod
    def from_msg(cls, msg: dict[str, object], websocket: WebSocket) -> Self:
        """Parse a wire message into a request, validating at the boundary.

        A string-typed field raises ``ValueError`` on a non-string value while a
        numeric field accepts a JSON number (via the parse helpers); empty or
        whitespace-only text, an ``api_key`` no HTTP header may carry, and a
        rate or voice setting :meth:`SynthesisSpec.validate` refuses are
        rejected here, so both handlers share one validation point.

        Raises:
            ValueError: If the message has no speech text or an invalid field.
        """
        text = parse_required_str(msg, "text")
        if not text.strip():
            raise ValueError("empty text")
        speaker_boost_raw = msg.get("speaker_boost")
        # ``provider`` is required on the wire (design §3.7): every client
        # surface (MCP, hook, CLI, panel, record) fills it from state via
        # :class:`SessionSpec` before crossing the wire. A hand-rolled
        # client that omits it gets an id-stamped rejection here rather
        # than a daemon-side guess .
        spec = SynthesisSpec(
            voice=parse_optional_str(msg, "voice"),
            provider=parse_required_str(msg, "provider"),
            model=parse_optional_str(msg, "model"),
            rate=parse_optional_int(msg, "rate"),
            language=parse_optional_str(msg, "language"),
            vibe_tags=parse_optional_str(msg, "vibe_tags"),
            stability=parse_optional_float(msg, "stability"),
            similarity=parse_optional_float(msg, "similarity"),
            style=parse_optional_float(msg, "style"),
            speaker_boost=(
                bool(speaker_boost_raw) if speaker_boost_raw is not None else None
            ),
            api_key=_REQUEST_KEY.require(parse_optional_str(msg, "api_key")),
        )
        spec.validate()
        request_id = str(msg.get("id", ""))
        return cls(text=text, spec=spec, request_id=request_id, websocket=websocket)

    async def synthesize(
        self,
        synthesis: SynthesisPipeline,
        *,
        # Runs before the error reply, so a retry the reply prompts cannot race
        # state the failure must undo (the synthesize handler's dedup entry).
        on_failure: Callable[[], None] = lambda: None,
    ) -> SynthesisOutcome | None:  # None: it failed, and the caller has been told
        """Synthesize this request to a file, replying with the error on failure.

        A failure replies through :meth:`fail`, so both handlers share one
        caller-error/fault split; only a daemon-side fault logs a traceback. F1
        and F7 never get here -- ``SessionSpec`` rejects them on the client --
        and F6 is a connection failure the client sees.
        """
        try:
            return await synthesis.synthesize_to_file(self.text, self.spec)
        except Exception as exc:
            if not isinstance(exc, _SYNTHESIS_REJECTIONS):
                logger.exception("Synthesis failed for id=%r", self.request_id)
            on_failure()
            await self.fail(exc)
        return None

    async def fail(self, result: Exception | int) -> None:
        """Reply to a failed synthesis, or a direct play that exited nonzero.

        Invalid speech and known provider errors are diagnosable and
        caller-side, so they cross the wire verbatim through
        :meth:`WireReply.error` rather than getting laundered to "operation failed" by
        :meth:`fault` (design §3.5). Any other exception, or a nonzero exit
        code, is a daemon-side fault.
        """
        if isinstance(result, _SYNTHESIS_REJECTIONS):
            await self._wire.error(result)
        elif isinstance(result, Exception):
            await self.fault(SafeFault.from_exception(result))
        else:
            await self.fault(SafeFault.opaque(f"play_directly failed with rc={result}"))

    async def reply(self, payload: dict[str, object]) -> None:
        """Send a success reply with the request id and any ignored-rate warning.

        Routing through :class:`WireReply` gives this request the same id-stamped,
        disconnect-safe send the store handlers use, instead of a raw
        ``send_json`` that raises out of the handler when the client has left.
        """
        await self._wire.audio(self.spec, payload)

    async def reply_duplicate(self, hit: DedupHit) -> None:
        """Validate and acknowledge a duplicate without a synthesis advisory."""
        try:
            SynthesisPipeline.normalized_text(self.text, self.spec)
        except EmptyNormalizedTextError as exc:
            await self.fail(exc)
            return
        logger.info(
            "Dedup hit: id=%r text=%d chars original=%.3f ttl_remaining=%.1fs",
            self.request_id,
            len(self.text),
            hit.original_played_at,
            hit.ttl_seconds_remaining,
        )
        await self._wire.send(
            {
                "type": "done",
                "deduped": True,
                "original_played_at": hit.original_played_at,
                "ttl_seconds_remaining": hit.ttl_seconds_remaining,
            }
        )

    async def fault(self, fault: SafeFault) -> None:
        """Audit a server-side OPERATIONAL failure and send a prefix-free frame.

        A failed synthesis or a nonzero direct-play exit is a daemon-side fault,
        not a client rejection, so it routes through :meth:`WireReply.fault` (the
        ERROR "operation failed" audit) -- matching the record handler's
        store-write fault -- never a WARNING "rejected op" that blames the client.
        The *fault* carries a prefix-free wire message and the raw log detail, so
        no absolute prefix reaches the client.
        """
        await self._wire.fault(fault)


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
        # Parse at the boundary: a non-string wire field (or empty text) is an
        # id-stamped error frame, never a ValueError that tears the connection down.
        try:
            req = _SpeechRequest.from_msg(msg, websocket)
            once = parse_optional_int(msg, "once") or 0
        except ValueError as exc:
            # WireReply makes a gone-peer send a clean no-op, matching the siblings.
            await SpeechReply(websocket, str(msg.get("id", ""))).error(exc)
            return

        await self._dispatch(req, once)

    async def _dispatch(self, req: _SpeechRequest, once: int) -> None:
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

    async def _play_local(self, req: _SpeechRequest, reservation: ExitStack) -> bool:
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
        self, req: _SpeechRequest, reservation: ExitStack
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
