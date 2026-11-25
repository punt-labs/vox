"""Record WebSocket handler: synthesize, store the file, return its id + path."""
# pyright: reportPrivateUsage=false
# Internal module within the voxd package -- cross-module private access is expected.

from __future__ import annotations

import asyncio
import logging
from functools import partial
from typing import TYPE_CHECKING, Self

from punt_vox.voxd._parse import parse_present_str
from punt_vox.voxd.speech_request import SpeechRequest
from punt_vox.voxd.synthesis import SynthesisPipeline
from punt_vox.voxd.types import MessageHandler
from punt_vox.voxd.wire_fault import SafeFault
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
        """Create a record handler."""
        self = super().__new__(cls)
        self._synthesis = synthesis
        self._store = store
        return self

    async def __call__(self, msg: dict[str, object], websocket: WebSocket) -> None:
        """Synthesize speech and store it under a daemon-owned name."""
        reply = WireReply(websocket, str(msg.get("id", "")))
        # Keep an explicit "" distinct from absence so only unnamed takes
        # content-address. Defer hashing unnamed takes to place().
        try:
            req = SpeechRequest.from_msg(msg, websocket)
            name = parse_present_str(msg, "name")
            await self._validate_name(name, req.text)
        except (ValueError, OSError) as exc:
            await reply.reject_or_fault(exc)
            return

        logger.info(
            "Record: id=%r chars=%d",
            req.request_id,
            len(req.text),
        )

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

        await self._record(req, name)

    async def _record(self, req: SpeechRequest, name: str | None) -> None:
        """Synthesize, land the file in the store, and send the terminal reply.

        Each failing step has already sent its own error frame, so a failure
        here only stops the pipeline.
        """
        outcome = await req.synthesize(self._synthesis)
        if outcome is None:
            return
        write = await self._store_outcome(req, name, outcome)
        if write is None:
            return
        await req.reply(
            {
                "type": "audio",
                "name": write.path.name,
                "path": write.wire_path,
                "bytes": write.byte_count,
                "cached": outcome.cached,
            }
        )

    async def _validate_name(self, name: str | None, text: str) -> None:
        """Check a supplied name before synthesis, including filesystem limits."""
        if name is not None:
            await asyncio.to_thread(self._store.resolve, name, text)

    async def _store_outcome(
        self, req: SpeechRequest, name: str | None, outcome: SynthesisOutcome
    ) -> RecordWrite | None:
        """Land the synthesized file in the store, or reply error and return None."""
        work = asyncio.get_running_loop().run_in_executor(
            None,
            partial(
                self._store.place,
                source=outcome.path,
                text=req.text,
                name=name,
            ),
        )
        try:
            write = await asyncio.shield(work)
        except asyncio.CancelledError:
            work.add_done_callback(outcome.discard_after)
            raise
        except Exception as exc:
            outcome.discard()
            # Any place() failure (not just OSError) must reach the already-ack'd
            # client as an error frame -- otherwise it waits out the full timeout.
            logger.exception("Record write failed for id=%r", req.request_id)
            await req.fault(SafeFault.from_exception(exc))
            return None
        outcome.discard()
        return write
