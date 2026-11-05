"""One chunked voxd fetch, reassembled and verified frame by frame.

voxd streams a stored file as ``fetch_begin`` -> ordered ``chunk``* ->
``fetch_end``. :class:`FetchAssembly` owns what that stream promised -- the
byte count, the chunk count, the sha256 -- and the bytes received so far, and
raises ``VoxdProtocolError`` the moment a frame breaks the promise: a frame out
of order, bad base64, a short or long stream, a digest mismatch.
"""

from __future__ import annotations

import base64
import hashlib
from typing import Any, Self, final

from punt_vox.client_errors import VoxdProtocolError

__all__ = ["FetchAssembly"]


@final
class FetchAssembly:
    """The bytes of one fetch, checked against its ``fetch_begin`` frame."""

    __slots__ = ("_buffer", "_chunks", "_declared", "_digest")

    _declared: int
    _chunks: int
    _digest: str
    _buffer: bytearray

    def __new__(cls, declared: int, chunks: int, digest: str) -> Self:
        self = super().__new__(cls)
        self._declared = declared
        self._chunks = chunks
        self._digest = digest
        self._buffer = bytearray()
        return self

    @classmethod
    def begin(cls, frame: dict[str, Any]) -> Self:  # a decoded wire frame
        """Return the assembly a ``fetch_begin`` frame announces."""
        if frame.get("type") != "fetch_begin":
            got = frame.get("type")
            raise VoxdProtocolError(f"expected 'fetch_begin', got {got!r}")
        try:
            declared, chunks = int(frame["bytes"]), int(frame["chunks"])
            digest = str(frame["sha256"])
        except (KeyError, TypeError, ValueError) as exc:
            raise VoxdProtocolError(f"malformed 'fetch_begin': {exc}") from exc
        return cls(declared, chunks, digest)

    @property
    def chunks(self) -> int:
        """Return how many ``chunk`` frames the stream announced."""
        return self._chunks

    def append(self, frame: dict[str, Any], seq: int) -> None:  # a decoded frame
        """Check chunk *seq*'s order and append its decoded bytes."""
        if frame.get("type") != "chunk" or frame.get("seq") != seq:
            raise VoxdProtocolError(
                f"expected chunk seq {seq}, got "
                f"{frame.get('type')!r} seq {frame.get('seq')!r}"
            )
        try:
            data = base64.b64decode(str(frame["data"]), validate=True)
        except (KeyError, ValueError, TypeError) as exc:
            raise VoxdProtocolError(f"chunk {seq} has invalid base64: {exc}") from exc
        self._buffer.extend(data)

    def finish(self, frame: dict[str, Any]) -> bytes:  # a decoded wire frame
        """Return the file once ``fetch_end`` arrives and the stream checks out."""
        if frame.get("type") != "fetch_end":
            raise VoxdProtocolError(f"expected 'fetch_end', got {frame.get('type')!r}")
        got = len(self._buffer)
        if got != self._declared:
            raise VoxdProtocolError(
                f"fetch byte-count mismatch: got {got}, declared {self._declared}"
            )
        if hashlib.sha256(self._buffer).hexdigest() != self._digest:
            raise VoxdProtocolError("fetch sha256 mismatch: stream corrupted")
        return bytes(self._buffer)
