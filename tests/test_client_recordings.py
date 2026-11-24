"""Tests for the paged recording listing over the client exchange."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from punt_vox.client import VoxClient
from punt_vox.client_errors import VoxdProtocolError
from punt_vox.client_exchange import VoxdExchange
from punt_vox.client_recordings import RecordingListing


def _make_mock_ws() -> AsyncMock:
    ws = AsyncMock()
    ws.transport = MagicMock()
    return ws


@pytest.mark.asyncio
@pytest.mark.parametrize("delays", [(4, 4), (6, 1), (4, 6)])
async def test_rec_list_times_out_only_when_a_page_stalls(
    delays: tuple[int, int],
) -> None:
    now = 0
    page = 0

    async def recv_before(deadline: float, expected: str) -> dict[str, object]:
        nonlocal now, page
        now += delays[page]
        if now > deadline:
            raise VoxdProtocolError(f"Timeout waiting for {expected}")
        frame = {
            "type": expected,
            "page": page,
            "more": page == 0,
            "entries": [{"name": f"{page}.mp3", "bytes": page}],
        }
        page += 1
        return frame

    exchange = MagicMock(spec=VoxdExchange)
    exchange.recv_before.side_effect = recv_before
    with patch("punt_vox.client_recordings.asyncio") as clock:
        clock.get_running_loop.return_value.time.side_effect = lambda: now
        if max(delays) > 5:
            with pytest.raises(VoxdProtocolError, match="Timeout waiting"):
                await RecordingListing.read(exchange, timeout=5)
        else:
            rows = await RecordingListing.read(exchange, timeout=5)
            assert [(row.name, row.byte_count) for row in rows] == [
                ("0.mp3", 0),
                ("1.mp3", 1),
            ]


@pytest.mark.asyncio
async def test_rec_list_collects_all_pages() -> None:
    mock_ws = _make_mock_ws()
    mock_ws.recv = AsyncMock(
        side_effect=[
            json.dumps(
                {
                    "type": "recordings_page",
                    "page": page,
                    "more": page == 0,
                    "entries": [{"name": name, "bytes": size}],
                }
            )
            for page, (name, size) in enumerate([("a.mp3", 5), ("b.mp3", 2)])
        ]
    )
    client = VoxClient(port=8421, token="tok")
    with patch(
        "punt_vox.client_transport.websockets.asyncio.client.connect",
        new_callable=AsyncMock,
        return_value=mock_ws,
    ):
        rows = await client.rec_list()
    assert [(r.name, r.byte_count) for r in rows] == [("a.mp3", 5), ("b.mp3", 2)]


@pytest.mark.asyncio
async def test_rec_list_discards_connection_on_missing_page() -> None:
    mock_ws = _make_mock_ws()
    mock_ws.recv = AsyncMock(
        side_effect=[
            json.dumps(
                {
                    "type": "recordings_page",
                    "page": page,
                    "more": True,
                    "entries": [],
                }
            )
            for page in (0, 2)
        ]
    )
    client = VoxClient(port=8421, token="tok")
    with (
        patch(
            "punt_vox.client_transport.websockets.asyncio.client.connect",
            new_callable=AsyncMock,
            return_value=mock_ws,
        ),
        pytest.raises(VoxdProtocolError, match="expected recordings page 1"),
    ):
        await client.rec_list()
    mock_ws.transport.abort.assert_called_once()
