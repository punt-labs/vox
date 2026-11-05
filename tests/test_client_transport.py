"""Tests for punt_vox.client_transport.VoxdTransport: connect and receive."""

from __future__ import annotations

import asyncio
import re
import socket
import tomllib
import traceback
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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
