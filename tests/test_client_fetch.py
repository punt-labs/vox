"""``FetchAssembly``: one chunked fetch, checked frame by frame against its begin."""

from __future__ import annotations

import base64
import hashlib

import pytest

from punt_vox.client_errors import VoxdProtocolError
from punt_vox.client_fetch import FetchAssembly


def _begin(blob: bytes, chunks: int) -> dict[str, object]:
    digest = hashlib.sha256(blob).hexdigest()
    return {
        "type": "fetch_begin",
        "bytes": len(blob),
        "chunks": chunks,
        "sha256": digest,
    }


def _chunk(seq: int, data: bytes) -> dict[str, object]:
    return {"type": "chunk", "seq": seq, "data": base64.b64encode(data).decode()}


def test_a_well_formed_stream_reassembles_the_file() -> None:
    assembly = FetchAssembly.begin(_begin(b"abcdef", 2))
    assert assembly.chunks == 2
    assembly.append(_chunk(0, b"abc"), 0)
    assembly.append(_chunk(1, b"def"), 1)
    assert assembly.finish({"type": "fetch_end"}) == b"abcdef"


def test_a_stream_that_does_not_open_with_fetch_begin_raises() -> None:
    with pytest.raises(VoxdProtocolError, match="expected 'fetch_begin'"):
        FetchAssembly.begin({"type": "chunk"})


def test_a_malformed_begin_raises() -> None:
    with pytest.raises(VoxdProtocolError, match="malformed 'fetch_begin'"):
        FetchAssembly.begin({"type": "fetch_begin", "bytes": "x"})


def test_a_chunk_out_of_order_raises() -> None:
    assembly = FetchAssembly.begin(_begin(b"ab", 2))
    with pytest.raises(VoxdProtocolError, match="expected chunk seq 0"):
        assembly.append(_chunk(1, b"b"), 0)


def test_bad_base64_raises() -> None:
    assembly = FetchAssembly.begin(_begin(b"ab", 1))
    with pytest.raises(VoxdProtocolError, match="invalid base64"):
        assembly.append({"type": "chunk", "seq": 0, "data": "!!"}, 0)


def test_a_short_stream_raises_on_finish() -> None:
    assembly = FetchAssembly.begin(_begin(b"abcdef", 1))
    assembly.append(_chunk(0, b"abc"), 0)
    with pytest.raises(VoxdProtocolError, match="byte-count mismatch"):
        assembly.finish({"type": "fetch_end"})


def test_a_corrupted_stream_raises_on_finish() -> None:
    assembly = FetchAssembly.begin(_begin(b"abc", 1))
    assembly.append(_chunk(0, b"abd"), 0)
    with pytest.raises(VoxdProtocolError, match="sha256 mismatch"):
        assembly.finish({"type": "fetch_end"})


def test_a_missing_fetch_end_raises() -> None:
    assembly = FetchAssembly.begin(_begin(b"", 0))
    with pytest.raises(VoxdProtocolError, match="expected 'fetch_end'"):
        assembly.finish({"type": "chunk"})
