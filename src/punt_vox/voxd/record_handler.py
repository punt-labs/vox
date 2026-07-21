"""Record WebSocket handler: synthesize, write the file, return its path."""
# pyright: reportPrivateUsage=false
# Internal module within the voxd package -- cross-module private access is expected.

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Self

from starlette.websockets import WebSocket, WebSocketDisconnect

from punt_vox.voxd._parse import parse_optional_str
from punt_vox.voxd.record_sink import RecordSink
from punt_vox.voxd.speech_handlers import _SpeechRequest
from punt_vox.voxd.synthesis import SynthesisPipeline
from punt_vox.voxd.types import MessageHandler

if TYPE_CHECKING:
    from punt_vox.voxd.synthesis_result import SynthesisOutcome

__all__ = ["RecordHandler"]

logger = logging.getLogger(__name__)


class RecordHandler(MessageHandler):
    """Handle 'record' messages: synthesize, write the file, return its path.

    No audio crosses the wire -- the daemon writes the synthesized MP3 to the
    caller's destination and replies with the path and byte count. An immediate
    'recording' ack lets a long synthesis run without the client's response
    timeout firing; the terminal 'audio' reply carries the landed path.
    """

    __slots__ = ("_synthesis",)

    _synthesis: SynthesisPipeline

    def __new__(cls, *, synthesis: SynthesisPipeline) -> Self:
        self = super().__new__(cls)
        self._synthesis = synthesis
        return self

    async def __call__(self, msg: dict[str, object], websocket: WebSocket) -> None:
        """Synthesize speech and write it to the caller's destination path."""
        req = _SpeechRequest.from_msg(msg, websocket)
        if not req.text:
            await req.error("empty text")
            return

        sink = await self._resolve_sink(msg, req)
        if sink is None:
            return

        logger.info(
            "Record: id=%r provider=%r voice=%r chars=%d",
            req.request_id,
            req.spec.provider or "",
            req.spec.voice or "",
            len(req.text),
        )

        # Ack immediately so a long synthesis does not trip the client's response
        # timeout; the client then waits for the terminal 'audio' frame.
        await self._safe_reply(req, {"type": "recording"})

        outcome = await self._synthesize(req)
        if outcome is None:
            return

        try:
            write = await asyncio.to_thread(
                sink.place, source=outcome.path, text=req.text, cached=outcome.cached
            )
        except Exception as exc:
            # Any place() failure (not just OSError) must reach the already-ack'd
            # client as an error frame -- otherwise it waits out the full timeout.
            logger.exception("Record write failed for id=%r", req.request_id)
            await self._safe_reply(req, {"type": "error", "message": str(exc)})
            return

        await self._safe_reply(
            req,
            {
                "type": "audio",
                "path": str(write.path),
                "bytes": write.byte_count,
                "cached": outcome.cached,
            },
        )

    @staticmethod
    async def _resolve_sink(
        msg: dict[str, object], req: _SpeechRequest
    ) -> RecordSink | None:
        """Parse and validate the wire destination into a sink, or reply + None.

        Runs before the ack so a bad destination never leaves the client waiting.
        The path parsing and absolute-validation are wrapped in a ValueError
        guard: untrusted wire input (e.g. a path with an embedded NUL) must
        surface as a clean error frame, never crash the connection with the
        client left hanging for a reply that never comes.
        """
        output_dir = parse_optional_str(msg, "output_dir")
        if not output_dir:
            await req.error("record requires output_dir")
            return None
        output_path = parse_optional_str(msg, "output_path")
        try:
            dir_path = Path(output_dir)
            # voxd's cwd is not the caller's shell; a relative path would land
            # the recording in the daemon's directory. Reject anything relative.
            if not dir_path.is_absolute():
                await req.error("record requires an absolute output_dir")
                return None
            explicit: Path | None = None
            if output_path:
                explicit = Path(output_path)
                if not explicit.is_absolute():
                    await req.error("record requires an absolute output_path")
                    return None
        except ValueError:
            await req.error("record has an invalid output path")
            return None
        return RecordSink(dir_path, explicit)

    async def _synthesize(self, req: _SpeechRequest) -> SynthesisOutcome | None:
        """Synthesize to a file, or reply with the error and return None."""
        try:
            return await self._synthesis.synthesize_to_file(req.text, req.spec)
        except Exception as exc:
            logger.exception("Record synthesis failed for id=%r", req.request_id)
            await self._safe_reply(req, {"type": "error", "message": str(exc)})
            return None

    @staticmethod
    async def _safe_reply(req: _SpeechRequest, payload: dict[str, object]) -> None:
        """Send a reply, treating a vanished client as a normal end-of-request.

        A client that disconnects mid-record (e.g. Ctrl-C during synthesis) must
        never crash the daemon or corrupt its send path. A WebSocketDisconnect is
        the expected closed-client signal and is silent; a RuntimeError from a
        send on an already-closed socket is logged at debug so a genuine send
        fault is not swallowed invisibly.
        """
        try:
            await req.reply(payload)
        except WebSocketDisconnect:
            pass
        except RuntimeError as exc:
            logger.debug(
                "record reply dropped for id=%r (client closed?): %s",
                req.request_id,
                exc,
            )
