"""Tests for the shared fieldless transport wire adapter."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest
from starlette.websockets import WebSocket

from punt_vox.voxd.programs.service import ProgramService
from punt_vox.voxd.programs.transport_handler import TransportHandler


@pytest.mark.parametrize(
    ("wire_type", "operation"),
    [
        ("program_next", "advance"),
        ("program_prev", "prev"),
        ("program_pause", "pause"),
        ("program_resume", "resume"),
    ],
)
async def test_dispatches_the_wire_type_to_its_bound_operation(
    wire_type: str, operation: str
) -> None:
    service = Mock(spec=ProgramService)
    action = getattr(service, operation)
    action.return_value = True
    websocket = AsyncMock(spec=WebSocket)
    handler = TransportHandler.handlers(service)[wire_type]

    await handler({"id": "1"}, websocket)

    action.assert_called_once_with()
    websocket.send_json.assert_awaited_once_with({"type": wire_type, "id": "1"})
