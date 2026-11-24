"""Tests for punt_vox.client_transport.VoxdTransport: connect and receive."""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import socket
import tomllib
import traceback
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import websockets
import websockets.asyncio.server

from punt_vox.client_errors import VoxdConnectionError, VoxdProtocolError
from punt_vox.client_transport import VoxdTransport

_PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def test_the_websockets_floor_accepts_the_proxy_keyword_connect_passes() -> None:
    """connect() passes ``proxy=None`` so the token never crosses a proxy.

    websockets accepts ``proxy=`` only from 15.0; on 14.x the call raises
    ``TypeError`` before connecting, which no connect handler classifies.
    """
    project = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))["project"]
    floors = [d for d in project["dependencies"] if d.startswith("websockets")]
    assert floors == ["websockets>=15.0"]


# The token is built at runtime so no traceback source line can carry it.
_TOKEN = "s3c" + "ret-token-value"


def _traceback_of(host: str, port: int) -> tuple[VoxdConnectionError, str]:
    transport = VoxdTransport(host, port, _TOKEN)
    with pytest.raises(VoxdConnectionError) as caught:
        asyncio.run(transport.connect())
    return caught.value, "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize("host", ["a@b", "h#x"])
def test_an_invalid_uri_is_never_chained_into_the_traceback(host: str) -> None:
    """``InvalidURI`` renders the whole URI, token included, so a traceback
    that chained it would print the token wherever the error is logged."""
    error, text = _traceback_of(host, 1)
    assert "invalid voxd URI" in str(error)
    assert error.__cause__ is None
    assert _TOKEN not in text


def test_a_cause_without_the_uri_stays_chained() -> None:
    """A refusal names no URI; it keeps its cause for diagnosis."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    error, text = _traceback_of("127.0.0.1", port)
    # all_errors=True: the refusal arrives as a group of per-address errors.
    assert isinstance(error.__cause__, ExceptionGroup)
    assert _TOKEN not in text


@pytest.mark.parametrize(("host", "port"), [("127.0.0.1", 99999), ("[::1", 1)])
def test_an_unparseable_endpoint_is_a_connection_error_not_a_raw_value_error(
    host: str, port: int
) -> None:
    """urllib's ``ValueError`` from the URI would escape every
    ``VoxdConnectionError`` handler as a raw traceback."""
    error, text = _traceback_of(host, port)
    assert type(error) is VoxdConnectionError
    assert _TOKEN not in text


@pytest.mark.parametrize("name", ["serve.port", "serve.token"])
def test_a_non_utf8_run_file_is_a_content_free_connection_error(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for var in ("VOXD_HOST", "VOXD_PORT", "VOXD_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr("punt_vox.client_endpoint._user_run_dir", lambda: tmp_path)
    (tmp_path / "serve.port").write_text("8421", encoding="utf-8")
    (tmp_path / "serve.token").write_text(_TOKEN, encoding="utf-8")
    (tmp_path / name).write_bytes(_TOKEN.encode() + b"\xff")

    transport = VoxdTransport(None, None, None)
    with pytest.raises(VoxdConnectionError) as caught:
        asyncio.run(transport.connect())

    assert type(caught.value) is VoxdConnectionError
    assert str(caught.value) == f"cannot read {tmp_path / name}: invalid UTF-8"
    rendered = "".join(traceback.format_exception(caught.value))
    assert "UnicodeDecodeError" not in rendered
    assert _TOKEN not in rendered


async def _never() -> str:
    await asyncio.Event().wait()
    return ""


@pytest.mark.parametrize(
    ("call", "expected"),
    [
        ("fetch", "Timeout waiting for 'fetch_begin' in 'rec_get'"),
        ("drain", "Timeout waiting for 'playing' in 'synthesize'"),
    ],
)
def test_a_silent_daemon_times_out_naming_the_frame_and_the_op(
    call: str, expected: str
) -> None:
    """Fetch and drain share one deadline receive and one timeout wording."""
    ws = MagicMock()
    ws.send = AsyncMock()
    ws.close = AsyncMock()
    ws.recv = _never
    transport = VoxdTransport("127.0.0.1", 1, _TOKEN)

    async def run() -> object:
        if call == "fetch":
            return await transport.fetch_stream({"type": "rec_get"}, timeout=0.05)
        return await transport.send_and_drain(
            {"type": "synthesize"}, timeout=0.05, early_terminal="playing"
        )

    with (
        patch(
            "punt_vox.client_transport.websockets.asyncio.client.connect",
            AsyncMock(return_value=ws),
        ),
        pytest.raises(VoxdProtocolError, match=re.escape(expected)),
    ):
        asyncio.run(run())


def test_a_non_object_playback_frame_is_a_protocol_error() -> None:
    ws = AsyncMock()
    ws.transport = MagicMock()
    ws.recv.side_effect = ['{"type":"playing"}', "1"]
    transport = VoxdTransport("127.0.0.1", 1, _TOKEN)

    with (
        patch(
            "punt_vox.client_transport.websockets.asyncio.client.connect",
            AsyncMock(return_value=ws),
        ),
        pytest.raises(VoxdProtocolError, match="expected a JSON object from voxd"),
    ):
        asyncio.run(transport.send_and_drain({"type": "synthesize"}, timeout=1))


class _LateRosterDaemon:
    """A real WebSocket server that answers the first roster request late.

    Every reply names the provider it answers, so a reused connection that
    reads a stale reply returns the wrong provider's roster.
    """

    def __init__(self, delay: float) -> None:
        self._delay = delay
        self._answered = 0

    async def handle(self, ws: websockets.asyncio.server.ServerConnection) -> None:
        with contextlib.suppress(websockets.exceptions.ConnectionClosed):
            async for raw in ws:
                msg = json.loads(raw)
                if self._answered == 0:
                    await asyncio.sleep(self._delay)
                self._answered += 1
                voices = [f"{msg['provider']}-voice"]
                await ws.send(json.dumps({"type": "done", "voices": voices}))


async def _timed_out_request(transport: VoxdTransport, how: str) -> None:
    msg: dict[str, object] = {"type": "voices", "provider": "elevenlabs"}
    if how == "recv":
        await transport.send_and_recv(msg, timeout=0.05)
    elif how == "drain":
        await transport.send_and_drain(msg, timeout=0.05)
    else:
        await asyncio.wait_for(transport.send_and_recv(msg, timeout=10), 0.05)


@pytest.mark.parametrize(
    ("how", "raised"),
    [
        ("recv", VoxdProtocolError),
        ("drain", VoxdProtocolError),
        ("cancel", TimeoutError),
    ],
)
def test_a_reused_transport_never_returns_a_timed_out_requests_late_reply(
    how: str, raised: type[Exception]
) -> None:
    """A late reply to an abandoned request must not answer the next one.

    The daemon still answers a request the client gave up on; if the
    connection stays open, that reply is the next frame the next request
    reads. Covers the deadline expiring in ``send_and_recv`` and
    ``send_and_drain`` and the caller cancelling (``BoundedCall``).
    """
    daemon = _LateRosterDaemon(delay=0.2)

    async def run() -> dict[str, object]:
        async with websockets.asyncio.server.serve(
            daemon.handle, "127.0.0.1", 0
        ) as server:
            port = server.sockets[0].getsockname()[1]
            transport = VoxdTransport("127.0.0.1", port, _TOKEN)
            try:
                with pytest.raises(raised):
                    await _timed_out_request(transport, how)
                await asyncio.sleep(0.3)  # the late reply has been sent
                msg: dict[str, object] = {"type": "voices", "provider": "polly"}
                return await transport.send_and_recv(msg, timeout=1)
            finally:
                await transport.close()

    assert asyncio.run(run())["voices"] == ["polly-voice"]


def test_a_failed_request_never_closes_a_connection_another_request_opened() -> None:
    """Discarding after a failure closes only the failed request's own socket.

    If another request reconnected while this one waited, the transport keeps
    that newer connection and the next request reuses it.
    """
    stale, fresh = AsyncMock(), AsyncMock()
    stale.transport, fresh.transport = MagicMock(), MagicMock()
    transport = VoxdTransport("127.0.0.1", 1, _TOKEN)

    async def reconnect_then_drop() -> str:
        await transport.connect()
        raise websockets.exceptions.ConnectionClosedError(None, None)

    stale.recv.side_effect = reconnect_then_drop
    fresh.recv.return_value = json.dumps({"type": "done", "voices": []})
    connect = AsyncMock(side_effect=[stale, fresh])
    msg: dict[str, object] = {"type": "voices", "provider": "polly"}

    async def run() -> dict[str, object]:
        with patch(
            "punt_vox.client_transport.websockets.asyncio.client.connect", connect
        ):
            with pytest.raises(VoxdConnectionError):
                await transport.send_and_recv(msg, timeout=1)
            return await transport.send_and_recv(msg, timeout=1)

    assert asyncio.run(run()) == {"type": "done", "voices": []}
    stale.transport.abort.assert_called_once_with()
    fresh.transport.abort.assert_not_called()
    assert connect.await_count == 2


def test_abandoning_a_request_never_waits_for_a_silent_peer_to_close() -> None:
    """A cancelled request returns at its deadline, not after a close handshake.

    A peer that stopped answering never completes the closing handshake, so
    discarding by ``close()`` would hold the caller for the close timeout after
    its own deadline; the abandoned socket is aborted instead.
    """
    ws = AsyncMock()
    ws.transport = MagicMock()
    ws.recv.side_effect = _never
    ws.close.side_effect = _never
    transport = VoxdTransport("127.0.0.1", 1, _TOKEN)
    msg: dict[str, object] = {"type": "health"}

    async def run() -> float:
        loop = asyncio.get_running_loop()
        started = loop.time()
        with (
            patch(
                "punt_vox.client_transport.websockets.asyncio.client.connect",
                AsyncMock(return_value=ws),
            ),
            pytest.raises(TimeoutError),
        ):
            await asyncio.wait_for(transport.send_and_recv(msg, timeout=10), 0.05)
        return loop.time() - started

    assert asyncio.run(run()) < 1
    ws.transport.abort.assert_called_once_with()
