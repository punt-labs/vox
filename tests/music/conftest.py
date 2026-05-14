"""Shared fixtures for music handler tests."""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.websockets import WebSocket

__all__: list[str] = []


def _make_mock_websocket() -> MagicMock:
    """Return a mock WebSocket with an async send_json."""
    ws: MagicMock = MagicMock(spec=WebSocket)
    ws.send_json = AsyncMock()
    return ws


@pytest.fixture
def make_ws() -> Callable[[], MagicMock]:
    """Fixture that returns a factory for mock WebSockets."""
    return _make_mock_websocket
