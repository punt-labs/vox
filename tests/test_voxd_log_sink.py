"""Tests for the daemon-side log sink (src/punt_vox/voxd/log_sink.py)."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from punt_vox.log_wire import LogRecordWire
from punt_vox.voxd.log_sink import LogHandler

if TYPE_CHECKING:
    import pytest


class _NullWs:
    """A stand-in websocket the send-only sink never touches."""


def _frame(message: str, *, level: str = "INFO") -> dict[str, object]:
    wire = LogRecordWire(
        role="hook", name="punt_vox.hooks", level=level, created=123.0, message=message
    )
    return wire.to_wire()


def _emit(frame: dict[str, object]) -> None:
    asyncio.run(LogHandler()(frame, _NullWs()))  # type: ignore[arg-type]


class TestLogHandler:
    """The sink re-emits shipped frames into vox.log, refusing malformed ones."""

    def test_shipped_message_not_reinterpolated(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A ``%s``/newline message is emitted verbatim (escaped), one physical line."""
        with caplog.at_level(logging.DEBUG, logger="client.hook.punt_vox.hooks"):
            _emit(_frame("100%s done\nforged INFO line"))
        records = [r for r in caplog.records if r.name == "client.hook.punt_vox.hooks"]
        assert len(records) == 1
        record = records[0]
        assert record.args is None  # never re-interpolated
        rendered = record.getMessage()
        assert rendered == "100%s done\\nforged INFO line"  # escaped, single line
        assert "\n" not in rendered

    def test_level_and_timestamp_preserved(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.DEBUG, logger="client.mcp.punt_vox.server"):
            wire = LogRecordWire(
                role="mcp",
                name="punt_vox.server",
                level="WARNING",
                created=999.5,
                message="voxd unreachable",
            )
            _emit(wire.to_wire())
        name = "client.mcp.punt_vox.server"
        record = next(r for r in caplog.records if r.name == name)
        assert record.levelname == "WARNING"
        assert record.created == 999.5

    def test_malformed_frame_refused_metadata_only(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A bad frame yields one WARNING naming the field/type, never the payload."""
        with caplog.at_level(logging.WARNING, logger="punt_vox.voxd.log_sink"):
            _emit({"role": "hook", "name": "n", "level": "INFO", "message": "secret"})
        warnings = [r for r in caplog.records if r.name == "punt_vox.voxd.log_sink"]
        assert len(warnings) == 1
        assert "created" in warnings[0].getMessage()  # names the missing field
        assert "secret" not in warnings[0].getMessage()  # never the payload
        emitted = [r for r in caplog.records if r.name.startswith("client.")]
        assert emitted == []  # nothing written for a rejected frame
