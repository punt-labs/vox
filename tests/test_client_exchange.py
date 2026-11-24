"""Tests for punt_vox.client_exchange.VoxdExchange: one request's frames."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import sys
from unittest.mock import AsyncMock, MagicMock, call

import pytest
import websockets

from punt_vox.client_errors import (
    VoxdConnectionError,
    VoxdProtocolError,
    VoxdRejectionError,
)
from punt_vox.client_exchange import VoxdExchange

_VOICES: dict[str, object] = {"type": "voices", "provider": "polly"}


def _exchange(*frames: object) -> tuple[VoxdExchange, AsyncMock]:
    """An exchange whose connection replies with *frames* in order."""
    ws = AsyncMock()
    ws.recv.side_effect = [json.dumps(frame) for frame in frames]
    return VoxdExchange(ws, _VOICES), ws


def _fetch_frames(blob: bytes, *, chunk: int) -> list[dict[str, object]]:
    """A valid ``fetch_begin`` -> ``chunk``* -> ``fetch_end`` stream for *blob*."""
    slices = [blob[i : i + chunk] for i in range(0, len(blob), chunk)]
    begin: dict[str, object] = {
        "type": "fetch_begin",
        "bytes": len(blob),
        "chunks": len(slices),
        "sha256": hashlib.sha256(blob).hexdigest(),
    }
    chunks: list[dict[str, object]] = [
        {"type": "chunk", "seq": seq, "data": base64.b64encode(data).decode()}
        for seq, data in enumerate(slices)
    ]
    return [begin, *chunks, {"type": "fetch_end"}]


def _later(seconds: float) -> float:
    return asyncio.get_running_loop().time() + seconds


def test_the_exchange_sends_its_request_as_one_json_frame() -> None:
    exchange, ws = _exchange()
    asyncio.run(exchange.send())
    ws.send.assert_awaited_once_with(json.dumps(_VOICES))
    assert exchange.connection is ws
    assert exchange.op == "voices"


def test_a_lost_connection_on_send_names_the_op() -> None:
    exchange, ws = _exchange()
    ws.send.side_effect = OSError("broken pipe")
    with pytest.raises(VoxdConnectionError, match="lost during 'voices'"):
        asyncio.run(exchange.send())


def test_the_reply_is_decoded() -> None:
    exchange, _ = _exchange({"type": "done", "voices": ["Joanna"]})
    reply = asyncio.run(exchange.reply(timeout=1))
    assert reply == {"type": "done", "voices": ["Joanna"]}


def test_a_daemon_error_reply_is_a_rejection() -> None:
    exchange, _ = _exchange({"type": "error", "message": "unknown provider"})
    with pytest.raises(VoxdRejectionError, match="unknown provider"):
        asyncio.run(exchange.reply(timeout=1))


def test_a_reply_that_is_not_json_is_a_protocol_error() -> None:
    with pytest.raises(VoxdProtocolError, match="invalid JSON from voxd"):
        VoxdExchange.decode("not json")


def test_a_reply_past_the_integer_conversion_limit_is_a_protocol_error() -> None:
    digits = sys.get_int_max_str_digits()
    if not digits:
        pytest.skip("Python integer conversion limit is disabled")
    with pytest.raises(VoxdProtocolError, match="invalid JSON from voxd"):
        VoxdExchange.decode("1" * (digits + 1))


def test_a_reply_nested_past_the_recursion_limit_is_a_protocol_error() -> None:
    depth = sys.getrecursionlimit() + 1
    with pytest.raises(VoxdProtocolError, match="invalid JSON from voxd"):
        VoxdExchange.decode("[" * depth + "0" + "]" * depth)


@pytest.mark.parametrize("frame", [[], 1, "x", True, None])
def test_a_json_reply_that_is_not_an_object_is_a_protocol_error(
    frame: object,
) -> None:
    exchange, _ = _exchange(frame)
    with pytest.raises(VoxdProtocolError, match="expected a JSON object from voxd"):
        asyncio.run(exchange.reply(timeout=1))


def test_a_silent_daemon_times_out_naming_the_op() -> None:
    exchange, ws = _exchange()

    async def never_answer() -> str:
        await asyncio.sleep(1)
        return ""

    ws.recv.side_effect = never_answer
    with pytest.raises(VoxdProtocolError, match="response to 'voices'"):
        asyncio.run(exchange.reply(timeout=0.01))


def test_a_closed_connection_while_waiting_names_the_op() -> None:
    exchange, ws = _exchange()
    ws.recv.side_effect = websockets.exceptions.ConnectionClosedError(None, None)
    with pytest.raises(VoxdConnectionError, match="lost during 'voices'"):
        asyncio.run(exchange.reply(timeout=1))


def test_a_passed_deadline_times_out_without_reading_a_frame() -> None:
    exchange, ws = _exchange({"type": "done"})

    async def run() -> object:
        return await exchange.recv_before(_later(-1), "done")

    with pytest.raises(VoxdProtocolError, match="'done' in 'voices'"):
        asyncio.run(run())
    ws.recv.assert_not_awaited()


def test_a_chunk_stream_is_written_one_chunk_at_a_time() -> None:
    exchange, _ = _exchange(*_fetch_frames(b"abcdefg", chunk=3))
    sink = MagicMock(spec=io.BytesIO)
    sink.write.side_effect = len

    async def run() -> int:
        return await exchange.receive_fetch(_later(1), sink)

    assert asyncio.run(run()) == 7
    assert sink.write.call_args_list == [call(b"abc"), call(b"def"), call(b"g")]


def test_a_short_sink_write_stops_the_fetch() -> None:
    exchange, ws = _exchange(*_fetch_frames(b"abcdef", chunk=3))
    sink = MagicMock(spec=io.BytesIO)
    sink.write.return_value = 2

    async def run() -> int:
        return await exchange.receive_fetch(_later(1), sink)

    with pytest.raises(OSError, match="incomplete write for fetch chunk 0"):
        asyncio.run(run())
    assert ws.recv.await_count == 2  # no read after the failed write


def test_a_non_object_fetch_frame_is_a_protocol_error() -> None:
    begin, _, end = _fetch_frames(b"abc", chunk=3)
    exchange, _ = _exchange(begin, [], end)

    async def run() -> int:
        return await exchange.receive_fetch(_later(1), io.BytesIO())

    with pytest.raises(VoxdProtocolError, match="expected a JSON object from voxd"):
        asyncio.run(run())


def test_an_out_of_order_chunk_is_a_protocol_error() -> None:
    begin, first, second, end = _fetch_frames(b"abcdef", chunk=3)
    exchange, _ = _exchange(begin, second, first, end)

    async def run() -> int:
        return await exchange.receive_fetch(_later(1), io.BytesIO())

    with pytest.raises(VoxdProtocolError, match="expected chunk seq 0"):
        asyncio.run(run())
