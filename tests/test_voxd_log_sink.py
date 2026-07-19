"""Tests for the daemon-side log sink (src/punt_vox/voxd/log_sink.py)."""

from __future__ import annotations

import asyncio
import io
import logging
from typing import TYPE_CHECKING

from punt_vox.log_sanitize import SanitizingFormatter
from punt_vox.log_wire import LOG_DATE_FORMAT, LOG_FORMAT, LogRecordWire
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
    """The sink re-emits shipped frames onto the fixed ``client`` logger."""

    def test_shipped_message_not_reinterpolated(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A ``%s`` message is emitted verbatim (args dropped), on the client logger."""
        with caplog.at_level(logging.DEBUG, logger="client"):
            _emit(_frame("100%s done"))
        records = [r for r in caplog.records if r.name == "client"]
        assert len(records) == 1
        record = records[0]
        assert record.args is None  # never re-interpolated
        assert record.getMessage() == "hook.punt_vox.hooks: 100%s done"  # origin folded

    def test_fixed_logger_name_regardless_of_origin(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Every shipped frame lands on the one ``client`` logger, not a per-name one.

        Streaming unique names must not intern a new logger per name (cache DoS):
        the origin rides in the message, the logger name is fixed.
        """
        with caplog.at_level(logging.DEBUG, logger="client"):
            _emit(_frame("a"))
            _emit(
                LogRecordWire(
                    role="mcp",
                    name="unique.abc.123",
                    level="INFO",
                    created=1.0,
                    message="b",
                ).to_wire()
            )
        assert {r.name for r in caplog.records} == {"client"}

    def test_level_and_timestamp_preserved(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.DEBUG, logger="client"):
            wire = LogRecordWire(
                role="mcp",
                name="punt_vox.server",
                level="WARNING",
                created=999.5,
                message="voxd unreachable",
            )
            _emit(wire.to_wire())
        record = next(r for r in caplog.records if r.name == "client")
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
        emitted = [r for r in caplog.records if r.name == "client"]
        assert emitted == []  # nothing written for a rejected frame


class TestSanitizedFileWrite:
    """The full formatted vox.log line is always one physical line (H2)."""

    def test_newline_and_control_in_name_stay_one_line(self) -> None:
        """A newline + raw control byte in ``name`` render as one escaped line."""
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(
            SanitizingFormatter(fmt=LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
        )
        client = logging.getLogger("client")
        saved_level = client.level
        saved_propagate = client.propagate
        client.addHandler(handler)
        client.setLevel(logging.DEBUG)
        client.propagate = False
        try:
            _emit(
                LogRecordWire(
                    role="hook",
                    name="mod\nFORGED [ERROR] evil\x07\x1b[31m",
                    level="INFO",
                    created=123.0,
                    message="hi\nalso forged\x00",
                ).to_wire()
            )
        finally:
            client.removeHandler(handler)
            # Restore BOTH mutated attributes so DEBUG doesn't leak into later tests.
            client.setLevel(saved_level)
            client.propagate = saved_propagate
        out = stream.getvalue()
        assert out.count("\n") == 1  # only the record terminator
        body = out[:-1]  # drop the terminator
        assert not any(ord(c) < 0x20 or 0x7F <= ord(c) < 0xA0 for c in body)
        assert "\\n" in body and "\\x07" in body  # escaped, not raw

    def test_role_with_newline_is_rejected_not_written(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A forged newline in ``role`` fails validation -- never reaches the file."""
        frame: dict[str, object] = {
            "role": "hook\nFORGED",
            "name": "n",
            "level": "INFO",
            "created": 123.0,
            "message": "m",
        }
        with caplog.at_level(logging.WARNING, logger="punt_vox.voxd.log_sink"):
            _emit(frame)
        warnings = [r for r in caplog.records if r.name == "punt_vox.voxd.log_sink"]
        assert len(warnings) == 1
        assert "role" in warnings[0].getMessage()
