"""One chunked fetch, checked frame by frame against its begin."""

from __future__ import annotations

import base64
import hashlib

import pytest

from punt_vox.client_errors import VoxdProtocolError
from punt_vox.client_fetch import FetchVerifier


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


def test_a_well_formed_stream_decodes_chunks_and_verifies_the_count() -> None:
    verifier = FetchVerifier.begin(_begin(b"abcdef", 2))
    assert verifier.chunks == 2
    assert verifier.decode_chunk(_chunk(0, b"abc"), 0) == b"abc"
    assert verifier.decode_chunk(_chunk(1, b"def"), 1) == b"def"
    assert verifier.finish({"type": "fetch_end"}) == 6


def test_an_empty_stream_verifies() -> None:
    verifier = FetchVerifier.begin(_begin(b"", 0))
    assert verifier.finish({"type": "fetch_end"}) == 0


def test_a_stream_that_does_not_open_with_fetch_begin_raises() -> None:
    with pytest.raises(VoxdProtocolError, match="expected 'fetch_begin'"):
        FetchVerifier.begin({"type": "chunk"})


def test_a_malformed_begin_raises() -> None:
    with pytest.raises(VoxdProtocolError, match="malformed 'fetch_begin'"):
        FetchVerifier.begin({"type": "fetch_begin", "bytes": "x"})


@pytest.mark.parametrize("field", ["bytes", "chunks"])
def test_negative_counts_raise(field: str) -> None:
    frame = _begin(b"", 0)
    frame[field] = -1
    with pytest.raises(VoxdProtocolError, match="negative byte or chunk count"):
        FetchVerifier.begin(frame)


def test_a_chunk_out_of_order_raises() -> None:
    verifier = FetchVerifier.begin(_begin(b"ab", 2))
    with pytest.raises(VoxdProtocolError, match="expected chunk seq 0"):
        verifier.decode_chunk(_chunk(1, b"b"), 0)


def test_bad_base64_raises() -> None:
    verifier = FetchVerifier.begin(_begin(b"ab", 1))
    with pytest.raises(VoxdProtocolError, match="invalid base64"):
        verifier.decode_chunk({"type": "chunk", "seq": 0, "data": "!!"}, 0)


def test_a_short_stream_raises_on_finish() -> None:
    verifier = FetchVerifier.begin(_begin(b"abcdef", 1))
    verifier.decode_chunk(_chunk(0, b"abc"), 0)
    with pytest.raises(VoxdProtocolError, match="byte-count mismatch"):
        verifier.finish({"type": "fetch_end"})


def test_an_oversized_chunk_raises_before_delivery() -> None:
    verifier = FetchVerifier.begin(_begin(b"abc", 2))
    verifier.decode_chunk(_chunk(0, b"ab"), 0)
    with pytest.raises(VoxdProtocolError, match="byte-count mismatch"):
        verifier.decode_chunk(_chunk(1, b"cd"), 1)


def test_a_corrupted_stream_raises_on_finish() -> None:
    verifier = FetchVerifier.begin(_begin(b"abc", 1))
    verifier.decode_chunk(_chunk(0, b"abd"), 0)
    with pytest.raises(VoxdProtocolError, match="sha256 mismatch"):
        verifier.finish({"type": "fetch_end"})


def test_a_missing_fetch_end_raises() -> None:
    verifier = FetchVerifier.begin(_begin(b"", 0))
    with pytest.raises(VoxdProtocolError, match="expected 'fetch_end'"):
        verifier.finish({"type": "chunk"})
