"""Stream a resolved store file to the client as bounded, ordered chunks.

``ChunkedTransfer`` is the daemon half of the ``get`` transport modelled in
``docs/vox-chunked-transfer.tex``: a ``fetch_begin`` announcing the total byte
count, chunk count, and sha256; then strictly-increasing ``chunk`` frames, each at
most ``FETCH_CHUNK_BYTES`` raw bytes; then a ``fetch_end`` -- or, on a mid-stream
read fault, an ``error`` terminal instead. The same open file is read in slices
twice (once to declare the totals and hash, once to send), so memory stays
bounded for a file of any size and the declared byte count is a guard the client
checks against.

Containment happens *before* a transfer is handed here: the caller resolves the
reference once and passes a ``Path``, so no byte is ever emitted for an
uncontained reference (the ``RejectBeforeBegin`` property).
"""

from __future__ import annotations

import base64
import logging
from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.wire_fault import SafeFault

if TYPE_CHECKING:
    from pathlib import Path

    from punt_vox.voxd.fetch_digest import FetchDigest, MeasuredFile
    from punt_vox.voxd.wire_reply import WireReply

__all__ = ["ChunkedTransfer"]

logger = logging.getLogger(__name__)


@final
class ChunkedTransfer:
    """Read a file in ``chunk_bytes`` slices and stream it as begin/chunk*/end."""

    __slots__ = ("_digest", "_reply")

    _reply: WireReply
    _digest: FetchDigest

    def __new__(cls, reply: WireReply, digest: FetchDigest) -> Self:
        """Create a chunked transfer."""
        self = super().__new__(cls)
        self._reply = reply
        self._digest = digest
        return self

    async def stream(self, path: Path, ref: str) -> None:
        """Announce the totals, send ordered chunks, and end -- or abort on a fault.

        A read fault while measuring is refused *before* ``fetch_begin`` (no
        totals announced, nothing sent); a fault mid-stream ends the exchange with
        an ``error`` terminal, so the client discards its partial and no complete
        file is ever claimed for an interrupted transfer.
        """
        try:
            measured = await self._digest.measure(path)
            with measured.handle:
                complete = await self._send_file(measured, ref)
        except OSError as exc:
            await self._fault(exc)
            return
        if complete:
            await self._reply.send(
                {"type": "fetch_end", "ref": ref, "bytes": measured.total}
            )

    async def _send_file(self, measured: MeasuredFile, ref: str) -> bool:
        if not await self._reply.send(
            {
                "type": "fetch_begin",
                "ref": ref,
                "bytes": measured.total,
                "chunks": measured.chunks,
                "sha256": measured.sha256,
            }
        ):
            return False
        logger.info(
            "Fetch begin: id=%r ref=%r bytes=%d chunks=%d",
            self._reply.request_id,
            ref,
            measured.total,
            measured.chunks,
        )
        return await self._pump(measured)

    async def _pump(self, measured: MeasuredFile) -> bool:
        """Send exactly ``total`` bytes, or return False if the peer disconnects.

        Bounded by the declared ``total``, not the live file size: a file grown
        after the measure pass transfers exactly its declared prefix.

        Raises:
            OSError: If reading fails or the file ends before the declared total.
        """
        handle = measured.handle
        total = measured.total
        seq = 0
        sent = 0
        while sent < total:
            slice_ = handle.read(min(self._digest.chunk_bytes, total - sent))
            if not slice_:
                msg = f"file changed mid-fetch: sent {sent} of {total} bytes"
                raise OSError(msg)
            if not await self._reply.send(
                {
                    "type": "chunk",
                    "seq": seq,
                    "data": base64.b64encode(slice_).decode("ascii"),
                }
            ):
                return False  # peer gone -- stop reading and encoding further
            seq += 1
            sent += len(slice_)
        return True

    async def _fault(self, exc: OSError) -> None:
        """Audit a failed read and send a terminal error without exposing paths."""
        await self._reply.fault(SafeFault.from_exception(exc))
