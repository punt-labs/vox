"""``BoundedCall``: one request on a fresh connection, held to a time bound."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from punt_vox.client_call import BoundedCall
from punt_vox.client_errors import VoxdProtocolError

if TYPE_CHECKING:
    from punt_vox.client import VoxClient


def _client() -> MagicMock:
    client = MagicMock()
    client.connect = AsyncMock()
    client.close = AsyncMock()
    return client


async def _answer(client: VoxClient) -> str:
    return "ok"


async def _hang(client: VoxClient) -> str:
    await asyncio.sleep(30)
    return "late"


def test_unbounded_call_returns_the_answer_and_closes() -> None:
    client = _client()
    assert asyncio.run(BoundedCall().run(client, _answer)) == "ok"
    client.connect.assert_awaited_once()
    client.close.assert_awaited_once()


def test_bounded_call_answering_in_time_returns_the_answer() -> None:
    client = _client()
    assert asyncio.run(BoundedCall(5.0).run(client, _answer)) == "ok"
    client.close.assert_awaited_once()


def test_a_hung_request_raises_at_the_bound_and_still_closes() -> None:
    client = _client()
    started = time.monotonic()
    with pytest.raises(VoxdProtocolError, match=r"no answer from voxd within 0\.05s"):
        asyncio.run(BoundedCall(0.05).run(client, _hang))
    assert time.monotonic() - started < 5.0
    client.close.assert_awaited_once()


def test_a_hung_connect_is_bounded_too() -> None:
    client = _client()

    async def slow_connect() -> None:
        await asyncio.sleep(30)

    client.connect.side_effect = slow_connect
    started = time.monotonic()
    with pytest.raises(VoxdProtocolError):
        asyncio.run(BoundedCall(0.05).run(client, _answer))
    assert time.monotonic() - started < 5.0


def test_a_hung_close_does_not_outlive_the_bound() -> None:
    """The answer is kept; a close the deadline cuts short is abandoned."""
    client = _client()

    async def slow_close() -> None:
        await asyncio.sleep(30)

    client.close.side_effect = slow_close
    started = time.monotonic()
    assert asyncio.run(BoundedCall(0.2).run(client, _answer)) == "ok"
    assert time.monotonic() - started < 5.0


def test_an_error_from_the_operation_is_not_rewritten() -> None:
    client = _client()

    async def refused(c: VoxClient) -> str:
        msg = "auth rejected (4401)"
        raise VoxdProtocolError(msg)

    with pytest.raises(VoxdProtocolError, match="auth rejected"):
        asyncio.run(BoundedCall(5.0).run(client, refused))
    client.close.assert_awaited_once()
