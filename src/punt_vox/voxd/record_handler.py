"""Record WebSocket handler: synthesize, store the file, return its id + path."""
# pyright: reportPrivateUsage=false
# Internal module within the voxd package -- cross-module private access is expected.

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Self

from punt_vox.voxd.speech_handlers import _SpeechRequest
from punt_vox.voxd.synthesis import SynthesisPipeline
from punt_vox.voxd.types import MessageHandler
from punt_vox.voxd.wire_reply import WireReply

if TYPE_CHECKING:
    from starlette.websockets import WebSocket

    from punt_vox.voxd.record_store import RecordStore, RecordWrite
    from punt_vox.voxd.synthesis_result import SynthesisOutcome

__all__ = ["RecordHandler"]

logger = logging.getLogger(__name__)


class RecordHandler(MessageHandler):
    """Handle 'record' messages: synthesize, store the file, return its locator.

    No audio crosses the wire and the client never names a daemon path. The
    daemon writes the synthesized MP3 into its own recordings store -- under a
    validated bare name or a content-addressed default -- and replies with the
    store id/name, the store path, and the byte count. An immediate 'recording'
    ack lets a long synthesis run without the client's response timeout firing;
    the terminal 'audio' reply carries the landed locator.
    """

    __slots__ = ("_store", "_synthesis")

    _synthesis: SynthesisPipeline
    _store: RecordStore

    def __new__(cls, *, synthesis: SynthesisPipeline, store: RecordStore) -> Self:
        self = super().__new__(cls)
        self._synthesis = synthesis
        self._store = store
        return self

    async def __call__(self, msg: dict[str, object], websocket: WebSocket) -> None:
        """Synthesize speech and store it under a daemon-owned name."""
        req = _SpeechRequest.from_msg(msg, websocket)
        # A client supplies at most a bare name; the daemon content-addresses
        # ONLY when the name is absent. Read it raw (not parse_optional_str,
        # which would collapse an explicit "" to None and silently
        # content-address it) so an explicit empty name reaches _accept and is
        # rejected. Accept only after empty text and the name are validated, so
        # a hostile name (absolute, traversing, separated, empty) or empty text
        # surfaces as a clean error frame *before* the ack.
        raw_name = msg.get("name")
        name = None if raw_name is None else str(raw_name)
        if not await self._accept(req, name):
            return

        logger.info(
            "Record: id=%r name=%r provider=%r voice=%r chars=%d",
            req.request_id,
            name,
            req.spec.provider,
            req.spec.voice,
            len(req.text),
        )

        reply = WireReply(req.websocket, req.request_id)
        # Ack immediately so a long synthesis does not trip the client's response
        # timeout; the client then waits for the terminal 'audio' frame. If the
        # ack could not be delivered the client is already gone -- skip synthesis
        # (a real provider cost) and the write rather than orphan a file for a
        # request nobody is waiting on.
        if not await reply.send({"type": "recording"}):
            logger.info(
                "Record client gone before ack for id=%r; skipping synthesis",
                req.request_id,
            )
            return

        outcome = await self._synthesize(req)
        if outcome is None:
            return

        write = await self._store_outcome(req, name, outcome)
        if write is None:
            return

        await reply.send(
            {
                "type": "audio",
                "name": write.path.name,
                "path": str(write.path),
                "bytes": write.byte_count,
                "cached": outcome.cached,
            }
        )

    async def _accept(self, req: _SpeechRequest, name: str | None) -> bool:
        """Reject empty text or a hostile name before the ack; else accept.

        Resolving the candidate name once here is the single pre-ack gate: it
        raises on any name that is absolute, separated, traversing, empty,
        NUL-bearing, or non-printable, which becomes a one-line error frame --
        logged as a rejected op so a blocked probe of the store is not silent.
        """
        reply = WireReply(req.websocket, req.request_id)
        if not req.text:
            await reply.error("empty text")
            return False
        # Only a client-supplied name carries hostile input to reject pre-ack.
        # The no-name case is content-addressed by place() daemon-side (no
        # hostile input), so skip the pre-ack resolve -- otherwise it would MD5
        # the full text here and again in place(), a double hash on the hot path
        # before the ack for a large record.
        if name is not None:
            try:
                self._store.resolve(name, req.text)
            except ValueError as exc:
                await reply.error(str(exc))
                return False
        return True

    async def _store_outcome(
        self, req: _SpeechRequest, name: str | None, outcome: SynthesisOutcome
    ) -> RecordWrite | None:
        """Land the synthesized file in the store, or reply error and return None."""
        try:
            return await asyncio.to_thread(
                self._store.place,
                source=outcome.path,
                text=req.text,
                name=name,
                cached=outcome.cached,
            )
        except Exception as exc:
            # Any place() failure (not just OSError) must reach the already-ack'd
            # client as an error frame -- otherwise it waits out the full timeout.
            logger.exception("Record write failed for id=%r", req.request_id)
            await WireReply(req.websocket, req.request_id).send(
                {"type": "error", "message": str(exc)}
            )
            return None

    async def _synthesize(self, req: _SpeechRequest) -> SynthesisOutcome | None:
        """Synthesize to a file, or reply with the error and return None."""
        try:
            return await self._synthesis.synthesize_to_file(req.text, req.spec)
        except Exception as exc:
            logger.exception("Record synthesis failed for id=%r", req.request_id)
            await WireReply(req.websocket, req.request_id).send(
                {"type": "error", "message": str(exc)}
            )
            return None
