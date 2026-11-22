"""The start reply budget covers composed provider launch operations."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from punt_vox.client_transport import VoxdTransport
from punt_vox.worker_client import WorkerClient


@pytest.mark.asyncio
@pytest.mark.parametrize(("operation", "delay"), [("start", 0.04), ("list", 0)])
async def test_composed_start_has_a_longer_reply_budget(
    operation: str, delay: float
) -> None:
    async def reply(message: dict[str, object], *, timeout: float) -> dict[str, object]:
        await asyncio.wait_for(asyncio.sleep(delay), timeout=timeout / 1000)
        return {"type": "worker_result", "id": message["id"], "result": "accepted"}

    with (
        patch.object(VoxdTransport, "connect", new_callable=AsyncMock),
        patch.object(VoxdTransport, "close", new_callable=AsyncMock),
        patch.object(VoxdTransport, "send_and_recv", side_effect=reply),
    ):
        assert await WorkerClient.request(operation) == "accepted"
