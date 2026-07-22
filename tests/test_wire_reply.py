"""Tests for punt_vox.voxd.wire_reply -- id-stamped sends and logged rejections."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

from starlette.websockets import WebSocketDisconnect

from punt_vox.voxd.wire_reply import WireReply

if TYPE_CHECKING:
    import pytest


def _capturing_ws() -> tuple[MagicMock, list[dict[str, object]]]:
    sent: list[dict[str, object]] = []

    async def _send(payload: dict[str, object]) -> None:
        sent.append(payload)

    ws = MagicMock()
    ws.send_json = AsyncMock(side_effect=_send)
    return ws, sent


def _warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.levelno == logging.WARNING]


class TestSend:
    """send stamps the request id and survives a vanished peer."""

    def test_stamps_request_id(self) -> None:
        ws, sent = _capturing_ws()
        delivered = asyncio.run(WireReply(ws, "r1").send({"type": "done"}))
        assert delivered is True
        assert sent == [{"id": "r1", "type": "done"}]

    def test_returns_false_when_peer_gone(self) -> None:
        ws = MagicMock()
        ws.send_json = AsyncMock(side_effect=WebSocketDisconnect())
        delivered = asyncio.run(WireReply(ws, "r1").send({"type": "done"}))
        assert delivered is False


class TestErrorLogging:
    """error audit-logs the rejection at WARNING and sends the error frame."""

    def test_logs_one_warning_with_request_id(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        ws, sent = _capturing_ws()
        with caplog.at_level(logging.WARNING):
            asyncio.run(WireReply(ws, "req-42").error("name must not be absolute"))
        records = _warnings(caplog)
        assert len(records) == 1
        assert "req-42" in records[0].getMessage()
        assert sent[-1] == {
            "id": "req-42",
            "type": "error",
            "message": "name must not be absolute",
        }

    def test_wire_message_is_verbatim_but_log_has_no_raw_control_chars(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        ws, sent = _capturing_ws()
        hostile = "evil\nINJECTED forged log line\r\tname"
        with caplog.at_level(logging.WARNING):
            asyncio.run(WireReply(ws, "r1").error(hostile))
        # The client frame carries the message verbatim; nothing is stripped.
        assert sent[-1]["message"] == hostile
        # The log line escapes the control characters -- no injection into vox.log.
        logged = _warnings(caplog)[-1].getMessage()
        assert "\n" not in logged
        assert "\r" not in logged
        assert "\t" not in logged
        assert "\\n" in logged

    def test_long_message_is_capped_in_the_log(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        ws, _sent = _capturing_ws()
        with caplog.at_level(logging.WARNING):
            asyncio.run(WireReply(ws, "r1").error("A" * 500))
        logged = _warnings(caplog)[-1].getMessage()
        assert logged.endswith("...")
        # A bounded (~120 char) field plus the id prefix, never the full 500.
        assert len(logged) < 200

    def test_logs_even_when_peer_gone(self, caplog: pytest.LogCaptureFixture) -> None:
        ws = MagicMock()
        ws.send_json = AsyncMock(side_effect=WebSocketDisconnect())
        with caplog.at_level(logging.WARNING):
            delivered = asyncio.run(WireReply(ws, "r1").error("gone"))
        assert delivered is False
        # The audit trail does not depend on the client still being connected.
        assert _warnings(caplog)
