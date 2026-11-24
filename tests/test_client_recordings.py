"""Tests for the paged recording listing over the client exchange."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from punt_vox.client import VoxClient
from punt_vox.client_errors import VoxdProtocolError


def _make_mock_ws() -> AsyncMock:
    ws = AsyncMock()
    ws.transport = MagicMock()
    return ws


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
