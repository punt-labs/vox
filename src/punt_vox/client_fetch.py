"""Verify one chunked voxd fetch without retaining its contents.

voxd streams a stored file as ``fetch_begin`` -> ordered ``chunk``* ->
``fetch_end``. :class:`FetchVerifier` owns what that stream promised -- the
byte count, the chunk count, the sha256 -- and checks each frame as it arrives. It
raises ``VoxdProtocolError`` the moment a frame breaks the promise: a frame out
of order, bad base64, a short or long stream, a digest mismatch.
"""

from __future__ import annotations

import base64
import hashlib
from typing import TYPE_CHECKING, Self, final

from punt_vox.client_errors import VoxdProtocolError

if TYPE_CHECKING:
    from _hashlib import HASH

__all__ = ["FetchVerifier"]


@final
class FetchVerifier:
    """Check one fetch against its ``fetch_begin`` frame."""

    __slots__ = ("_chunks", "_declared", "_digest", "_hash", "_received")

    _declared: int
    _chunks: int
    _digest: str
    _hash: HASH
    _received: int

    def __new__(cls, declared: int, chunks: int, digest: str) -> Self:
        """Start counting and hashing a fetch with the declared metadata."""
        if declared < 0 or chunks < 0:
            raise VoxdProtocolError("negative byte or chunk count in 'fetch_begin'")
        self = super().__new__(cls)
        self._declared = declared
        self._chunks = chunks
        self._digest = digest
        self._hash = hashlib.sha256()
        self._received = 0
        return self

    @classmethod
    def begin(cls, frame: dict[str, object]) -> Self:  # a decoded wire frame
        """Return a verifier for the stream a ``fetch_begin`` frame announces.

        Raises:
            VoxdProtocolError: If the opening frame or its metadata is invalid.
        """
        if frame.get("type") != "fetch_begin":
            got = frame.get("type")
            raise VoxdProtocolError(f"expected 'fetch_begin', got {got!r}")
        try:
            declared, chunks = int(str(frame["bytes"])), int(str(frame["chunks"]))
            digest = str(frame["sha256"])
        except (KeyError, TypeError, ValueError) as exc:
            raise VoxdProtocolError(f"malformed 'fetch_begin': {exc}") from exc
        return cls(declared, chunks, digest)

    @property
    def chunks(self) -> int:
        """Return how many ``chunk`` frames the stream announced."""
        return self._chunks

    def decode_chunk(self, frame: dict[str, object], seq: int) -> bytes:
        """Check chunk *seq* and return its decoded bytes.

        Raises:
            VoxdProtocolError: If the chunk frame, sequence number, or encoded data is
                invalid, or the bytes exceed the announced size.
        """
        data = self._chunk_bytes(frame, seq)
        self._received += len(data)
        if self._received > self._declared:
            raise self._size_mismatch()
        self._hash.update(data)
        return data

    @staticmethod
    def _chunk_bytes(frame: dict[str, object], seq: int) -> bytes:
        """Decode the payload only after validating frame type and sequence."""
        if frame.get("type") != "chunk" or frame.get("seq") != seq:
            raise VoxdProtocolError(
                f"expected chunk seq {seq}, got "
                f"{frame.get('type')!r} seq {frame.get('seq')!r}"
            )
        try:
            return base64.b64decode(str(frame["data"]), validate=True)
        except (KeyError, ValueError, TypeError) as exc:
            raise VoxdProtocolError(f"chunk {seq} has invalid base64: {exc}") from exc

    def finish(self, frame: dict[str, object]) -> int:  # a decoded wire frame
        """Return the byte count once ``fetch_end`` arrives and the stream checks out.

        Raises:
            VoxdProtocolError: If the terminal frame, byte count, or digest is invalid.
        """
        if frame.get("type") != "fetch_end":
            raise VoxdProtocolError(f"expected 'fetch_end', got {frame.get('type')!r}")
        if self._received != self._declared:
            raise self._size_mismatch()
        if self._hash.hexdigest() != self._digest:
            raise VoxdProtocolError("fetch sha256 mismatch: stream corrupted")
        return self._received

    def _size_mismatch(self) -> VoxdProtocolError:
        """Report a stream that disagrees with the announced size."""
        return VoxdProtocolError(
            f"fetch byte-count mismatch: got {self._received}, "
            f"declared {self._declared}"
        )
