"""Tests for punt_vox.voxd._parse -- wire-frame helpers (parse + safe_send)."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

from starlette.websockets import WebSocketDisconnect

from punt_vox.voxd._parse import safe_send

if TYPE_CHECKING:
    import pytest

_LOGGER = "punt_vox.voxd._parse"


class TestSafeSend:
    """safe_send never lets a client disconnect escape, and logs with context."""

    def test_delivered_returns_true(self) -> None:
        ws = MagicMock()
        ws.send_json = AsyncMock()
        assert asyncio.run(safe_send(ws, {"type": "done", "id": "r1"})) is True

    def test_disconnect_drop_log_carries_frame_context(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A dropped reply is debug-logged with the frame's type and id, no raise."""
        ws = MagicMock()
        ws.send_json = AsyncMock(side_effect=WebSocketDisconnect())

        with caplog.at_level(logging.DEBUG, logger=_LOGGER):
            delivered = asyncio.run(
                safe_send(ws, {"type": "audio", "id": "r1", "name": "x.mp3"})
            )

        assert delivered is False  # a normal disconnect is a quiet end, not a raise
        assert "audio" in caplog.text  # which frame
        assert "r1" in caplog.text  # which request
        assert "x.mp3" in caplog.text  # which recording

    def test_runtime_error_drop_log_carries_frame_context(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        ws = MagicMock()
        ws.send_json = AsyncMock(side_effect=RuntimeError("socket closed"))

        with caplog.at_level(logging.DEBUG, logger=_LOGGER):
            delivered = asyncio.run(safe_send(ws, {"type": "bytes", "ref": "y.mp3"}))

        assert delivered is False
        assert "bytes" in caplog.text
        assert "y.mp3" in caplog.text
        assert "socket closed" in caplog.text
