"""Tests for the log-shipping wire schema (src/punt_vox/log_wire.py)."""

from __future__ import annotations

import logging

import pytest

from punt_vox.log_wire import (
    _MAX_MESSAGE_CHARS,
    _MAX_META_CHARS,
    LOG_MESSAGE_TYPE,
    LogRecordWire,
)


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

    def test_from_record_truncates_a_long_message_so_it_is_never_lost(self) -> None:
        """A 20 KB log line ships preserved-truncated and the daemon accepts it.

        Without client truncation the daemon's length cap would reject the whole
        frame and the line would land in neither vox.log nor the fallback.
        """
        wire = LogRecordWire.from_record(_record("X" * 20_000), role="hook")
        assert len(wire.message) <= _MAX_MESSAGE_CHARS  # within the cap
        assert wire.message.startswith("XXXX")  # head preserved
        assert wire.message.endswith(" chars]")  # truncation marker appended
        assert "…[+" in wire.message
        # The clamped frame passes the daemon's defense-in-depth length check.
        assert LogRecordWire.from_wire(wire.to_wire()).message == wire.message

    def test_from_record_truncates_a_long_name(self) -> None:
        record = logging.LogRecord(
            name="m" * 1000,
            level=logging.INFO,
            pathname="f",
            lineno=1,
            msg="hi",
            args=(),
            exc_info=None,
        )
        wire = LogRecordWire.from_record(record, role="hook")
        assert len(wire.name) <= _MAX_META_CHARS
        assert wire.name.endswith(" chars]")

    def test_from_record_leaves_short_fields_unchanged(self) -> None:
        wire = LogRecordWire.from_record(_record("short"), role="hook")
        assert wire.message == "short"  # no marker on a value within the cap

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
            {"role": "unknown"},  # L2: not a known role
            {"role": "hook\nFORGED"},  # L2: a forged newline in role is refused
            {"created": float("inf")},  # L2: non-finite created
            {"created": float("nan")},  # L2: nan created
            {"created": -1.0},  # L2: below the sane epoch window
            {"created": 9e18},  # L2: above the sane epoch window
            {"message": "x" * 8193},  # MED2: message over the 8 KiB cap
            {"name": "x" * 257},  # MED2: metadata over the 256-char cap
            {"role": "x" * 257},  # MED2: oversized role
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

    def test_from_wire_accepts_every_known_role(self) -> None:
        for role in ("hook", "mcp", "cli", "playback"):
            frame: dict[str, object] = {
                "role": role,
                "name": "n",
                "level": "INFO",
                "created": 1.0,
                "message": "m",
            }
            assert LogRecordWire.from_wire(frame).role == role

    def test_oversized_field_error_is_metadata_only(self) -> None:
        """An oversized field is refused by length -- never echoing its content."""
        secret = "leak-me-" + "z" * 9000
        frame: dict[str, object] = {
            "role": "hook",
            "name": "n",
            "level": "INFO",
            "created": 1.0,
            "message": secret,
        }
        with pytest.raises(ValueError, match="too long") as exc_info:
            LogRecordWire.from_wire(frame)
        assert "leak-me" not in str(exc_info.value)  # content never in the error

    def test_invalid_role_error_is_metadata_only(self) -> None:
        """A forged/unknown role is refused by length -- the raw value never echoed."""
        frame: dict[str, object] = {
            "role": "evil-role-\nFORGED",
            "name": "n",
            "level": "INFO",
            "created": 1.0,
            "message": "m",
        }
        with pytest.raises(ValueError, match="not a known role") as exc_info:
            LogRecordWire.from_wire(frame)
        assert "FORGED" not in str(exc_info.value)  # never the raw role

    def test_within_cap_message_is_accepted(self) -> None:
        """A message at the cap boundary is valid -- the bound is inclusive."""
        frame: dict[str, object] = {
            "role": "hook",
            "name": "n",
            "level": "INFO",
            "created": 1.0,
            "message": "x" * 8192,
        }
        assert len(LogRecordWire.from_wire(frame).message) == 8192

    def test_message_carries_raw_newline_for_the_sink_to_escape(self) -> None:
        """The wire holds the raw rendered text; escaping is each sink's job."""
        wire = LogRecordWire.from_record(_record("a\nb"), role="hook")
        assert wire.message == "a\nb"  # JSON transport encodes it safely
        assert LogRecordWire.from_wire(wire.to_wire()).message == "a\nb"
