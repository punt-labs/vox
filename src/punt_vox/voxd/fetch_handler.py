"""Fetch WebSocket handler: return a stored recording's bytes to a remote client.

``vox fetch <id> -o <path>`` materializes a store recording on a client that
does not share the daemon's filesystem. The reference is a bare store name,
resolved and containment-checked exactly like a record name -- no client path,
no escape. The bytes are returned base64-encoded in a **single frame**, so a
recording larger than the frame budget is refused with a clear error rather than
silently truncated. Remote fetch of a large recording is out of scope for this
cut (the same limit that already made remote record above ~1 MiB non-functional);
a chunked streaming transport is a separate, formally-modelled follow-up.
"""

from __future__ import annotations

import base64
import logging
from typing import TYPE_CHECKING, Self

from punt_vox.types_audio import FETCH_FRAME_LIMIT_BYTES
from punt_vox.voxd._parse import parse_optional_str
from punt_vox.voxd.types import MessageHandler
from punt_vox.voxd.wire_reply import WireReply

if TYPE_CHECKING:
    from pathlib import Path

    from starlette.websockets import WebSocket

    from punt_vox.voxd.record_store import RecordStore

__all__ = ["FetchHandler"]

logger = logging.getLogger(__name__)


class FetchHandler(MessageHandler):
    """Handle 'fetch' messages: return a store recording's bytes in one frame."""

    __slots__ = ("_store",)

    _store: RecordStore

    def __new__(cls, *, store: RecordStore) -> Self:
        self = super().__new__(cls)
        self._store = store
        return self

    async def __call__(self, msg: dict[str, object], websocket: WebSocket) -> None:
        """Resolve a store reference, then return its bytes or an error frame."""
        reply = WireReply(websocket, str(msg.get("id", "")))
        ref = parse_optional_str(msg, "ref")
        if not ref:
            await reply.error("fetch requires a ref")
            return
        try:
            path = self._store.resolve_ref(ref)
        except ValueError as exc:
            await reply.error(str(exc))
            return
        if not path.is_file():
            await reply.error(f"no recording named {ref!r}")
            return
        await self._read_and_send(reply, path, ref)

    async def _read_and_send(self, reply: WireReply, path: Path, ref: str) -> None:
        """Read *path* bounded to one frame and send its bytes, or an error frame."""
        try:
            # A cheap pre-read stat rejects the common oversize case first, but
            # it is NOT trusted for the read: a token-holding remote caller can
            # grow/replace the store file between this stat and the read
            # (record + fetch run concurrently), so reading the whole file would
            # let it drive an arbitrarily large allocation -- a memory/DoS
            # vector. Read at most FETCH_FRAME_LIMIT_BYTES + 1 so the worst-case
            # allocation is bounded regardless of the race; len > limit means
            # the on-disk file exceeds the budget and is rejected as oversize.
            prelim_size = path.stat().st_size
            if prelim_size > FETCH_FRAME_LIMIT_BYTES:
                await self._reject_oversize(reply, prelim_size)
                return
            with path.open("rb") as handle:
                raw = handle.read(FETCH_FRAME_LIMIT_BYTES + 1)
        except OSError as exc:
            # A read fault (the file deleted between is_file() and here, or a
            # permission/IO error) is a resource failure, not a rejected probe:
            # log it once here and send without re-logging via reply.error.
            logger.warning(
                "Fetch read failed for id=%r ref=%r: %s", reply.request_id, ref, exc
            )
            await reply.send(
                {"type": "error", "message": f"cannot read recording {ref!r}: {exc}"}
            )
            return

        # Authoritative size = what we read (capped at limit + 1). A file grown
        # past the limit after the stat is rejected here -- the read never held
        # more than limit + 1 bytes -- and the client's byte-count check is
        # compared against a declaration that matches the payload, never a stale
        # stat.
        size = len(raw)
        if size > FETCH_FRAME_LIMIT_BYTES:
            await self._reject_oversize(reply, size)
            return

        logger.info("Fetch: id=%r ref=%r bytes=%d", reply.request_id, ref, size)
        data = base64.b64encode(raw).decode("ascii")
        # Echo the REQUESTED ref, not path.name: on a case-insensitive
        # filesystem a mixed-case ref resolves to a differently-cased on-disk
        # name, and the client's exact-match check would spuriously fail after
        # a successful read. The ref was already validated by resolve_ref.
        await reply.send({"type": "bytes", "ref": ref, "data": data, "bytes": size})

    @staticmethod
    async def _reject_oversize(reply: WireReply, size: int) -> None:
        """Send the too-large-to-fetch error frame for a *size*-byte recording.

        An oversize recording is a legitimate too-large file, not a probe, so it
        is logged at INFO -- distinct from the WARNING class used for a rejected
        or failed op -- keeping the audit trail symmetric with the read-fault
        path, which also logs.
        """
        logger.info("Fetch rejected oversize: id=%r bytes=%d", reply.request_id, size)
        await reply.send(
            {
                "type": "error",
                "message": (
                    f"recording too large to fetch in one frame ({size} bytes > "
                    f"{FETCH_FRAME_LIMIT_BYTES}); retrieve it from the host directly"
                ),
            }
        )
