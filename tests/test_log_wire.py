"""Tests for the log-shipping wire schema (src/punt_vox/log_wire.py)."""

from __future__ import annotations

import logging

import pytest

from punt_vox.log_wire import LOG_MESSAGE_TYPE, LogRecordWire


def _record(msg: str, *args: object, level: int = logging.INFO) -> logging.LogRecord:
    return logging.LogRecord(
        name="punt_vox.hooks",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=args,
        exc_info=None,
    )


class TestLogRecordWire:
    """The frame captures a client record and round-trips through JSON."""

    def test_from_record_interpolates_on_the_client(self) -> None:
        wire = LogRecordWire.from_record(_record("played %d chimes", 3), role="hook")
        assert wire.message == "played 3 chimes"  # final rendered, args dropped
        assert wire.role == "hook"
        assert wire.name == "punt_vox.hooks"
        assert wire.level == "INFO"

    def test_to_wire_carries_the_log_type(self) -> None:
        wire = LogRecordWire.from_record(_record("hi"), role="mcp")
        frame = wire.to_wire()
        assert frame["type"] == LOG_MESSAGE_TYPE
        assert frame["role"] == "mcp"
        assert frame["message"] == "hi"

    def test_round_trip_preserves_fields(self) -> None:
        wire = LogRecordWire.from_record(_record("x"), role="cli")
        restored = LogRecordWire.from_wire(wire.to_wire())
        assert restored == wire

    def test_qualified_name_tags_origin(self) -> None:
        wire = LogRecordWire.from_record(_record("x"), role="hook")
        assert wire.qualified_name == "client.hook.punt_vox.hooks"

    def test_format_line_matches_standard_shape(self) -> None:
        wire = LogRecordWire(
            role="hook",
            name="punt_vox.hooks",
            level="INFO",
            created=0.0,
            message="Stop hook: blocking",
        )
        line = wire.format_line()
        assert " [INFO] client.hook.punt_vox.hooks: Stop hook: blocking" in line

    @pytest.mark.parametrize(
        "override",
        [
            {"created": None},  # absent number
            {"role": 3},  # non-string role
            {"created": "no"},  # non-numeric created
            {"created": True},  # bool is not a number
        ],
    )
    def test_from_wire_raises_on_malformed(self, override: dict[str, object]) -> None:
        raw: dict[str, object] = {
            "role": "hook",
            "name": "n",
            "level": "INFO",
            "created": 1.0,
            "message": "m",
        }
        raw.update(override)
        if override.get("created") is None and "created" in override:
            del raw["created"]
        with pytest.raises(ValueError, match="log frame field"):
            LogRecordWire.from_wire(raw)

    def test_message_carries_raw_newline_for_the_sink_to_escape(self) -> None:
        """The wire holds the raw rendered text; escaping is each sink's job."""
        wire = LogRecordWire.from_record(_record("a\nb"), role="hook")
        assert wire.message == "a\nb"  # JSON transport encodes it safely
        assert LogRecordWire.from_wire(wire.to_wire()).message == "a\nb"
