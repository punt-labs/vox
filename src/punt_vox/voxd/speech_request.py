"""Speech request parsing and replies shared by synthesis and recording."""

from __future__ import annotations

import logging
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
from punt_vox.voxd.speech_reply import SpeechReply
from punt_vox.voxd.synthesis import (
    _SYNTHESIS_REJECTIONS,  # pyright: ignore[reportPrivateUsage]
    EmptyNormalizedTextError,
    SynthesisPipeline,
)
from punt_vox.voxd.wire_fault import SafeFault

if TYPE_CHECKING:
    from collections.abc import Callable

    from starlette.websockets import WebSocket

    from punt_vox.voxd.dedup import DedupHit
    from punt_vox.voxd.synthesis_result import SynthesisOutcome

__all__ = ["SpeechRequest"]

logger = logging.getLogger(__name__)

# A per-request key is checked where it enters the daemon, before any provider
# puts it in a header; the message names the wire field, never the key.
_REQUEST_KEY = ApiKeyCheck("the request's api_key")


@dataclass(frozen=True, slots=True)
class SpeechRequest:
    """Bundle parsed synthesis fields and their ID-stamped reply channel."""

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
        numeric field accepts a JSON number (via the parse helpers); empty,
        whitespace-only or non-UTF-8 text, an ``api_key`` no HTTP header may
        carry, and any rate or voice settings :meth:`SynthesisSpec.validate`
        refuses are rejected here, so both handlers share one validation point.

        Raises:
            ValueError: If the message has no speech text or an invalid field.
        """
        text = parse_required_str(msg, "text")
        if not text.strip():
            raise ValueError("empty text")
        try:
            text.encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError("text must be valid UTF-8") from None
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
