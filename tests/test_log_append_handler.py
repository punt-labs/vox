"""Tests for the direct-append log handler (src/punt_vox/log_append_handler.py)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from punt_vox.append_log import AtomicAppendLog
from punt_vox.log_append_handler import AppendLogHandler

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _record(name: str, msg: str, *args: object) -> logging.LogRecord:
    """Build a bare INFO record on *name* with *msg* % *args*."""
    return logging.LogRecord(name, logging.INFO, "", 0, msg, args, None)


class TestAppendLogHandler:
    """The handler renders one line per record and appends it to the shared sink."""

    def test_daemon_record_keeps_its_own_logger_name(self, tmp_path: Path) -> None:
        sink = AtomicAppendLog(tmp_path / "vox.log")
        handler = AppendLogHandler.bind(sink)  # empty prefix == daemon
        handler.emit(_record("punt_vox.voxd", "daemon up"))
        line = sink.path.read_text(encoding="utf-8").rstrip("\n")
        assert line.endswith("[INFO] punt_vox.voxd: daemon up")

    def test_client_record_carries_role_prefix(self, tmp_path: Path) -> None:
        sink = AtomicAppendLog(tmp_path / "vox.log")
        handler = AppendLogHandler.bind(sink, name_prefix="client.hook.")
        handler.emit(_record("punt_vox.hooks", "stop skipped"))
        line = sink.path.read_text(encoding="utf-8").rstrip("\n")
        assert line.endswith("[INFO] client.hook.punt_vox.hooks: stop skipped")

    def test_prefix_does_not_mutate_the_shared_record(self, tmp_path: Path) -> None:
        """A later handler must see the original logger name (prefix is render-only)."""
        sink = AtomicAppendLog(tmp_path / "vox.log")
        handler = AppendLogHandler.bind(sink, name_prefix="client.cli.")
        record = _record("punt_vox.core", "hi")
        handler.emit(record)
        assert record.name == "punt_vox.core"  # restored after the render

    def test_message_interpolation_happens_once(self, tmp_path: Path) -> None:
        sink = AtomicAppendLog(tmp_path / "vox.log")
        handler = AppendLogHandler.bind(sink)
        handler.emit(_record("m", "spoke %d chars with %s", 5, "say"))
        assert "spoke 5 chars with say" in sink.path.read_text(encoding="utf-8")

    def test_logging_opens_no_socket(self, tmp_path: Path) -> None:
        """emit touches only the filesystem -- no daemon round-trip (DES-017).

        A sink that raises if any network attribute is read would fail; the plain
        filesystem sink proves the hot path is local. The file gaining the line is
        the positive proof there is no transport in the path.
        """
        sink = AtomicAppendLog(tmp_path / "vox.log")
        handler = AppendLogHandler.bind(sink)
        handler.emit(_record("m", "no socket here"))
        assert (tmp_path / "vox.log").exists()
        assert "no socket here" in sink.path.read_text(encoding="utf-8")

    def test_bad_format_args_never_raise(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A %-arg mismatch is absorbed by handleError, never crashing the caller."""
        sink = AtomicAppendLog(tmp_path / "vox.log")
        handler = AppendLogHandler.bind(sink)
        # raiseExceptions False keeps handleError quiet; too few args make
        # getMessage raise, and emit must swallow it rather than propagate.
        monkeypatch.setattr(logging, "raiseExceptions", False)
        handler.emit(_record("m", "needs %d and %d", 1))  # no exception
        assert not (tmp_path / "vox.log").exists()  # the malformed record is dropped

    def test_control_bytes_stay_one_line(self, tmp_path: Path) -> None:
        """A smuggled newline is escaped by the sink -- the record stays one line."""
        sink = AtomicAppendLog(tmp_path / "vox.log")
        handler = AppendLogHandler.bind(sink)
        handler.emit(_record("m", "line one\nforged second"))
        raw = (tmp_path / "vox.log").read_bytes()
        assert raw.count(b"\n") == 1  # only the terminator -- no forged line

    def test_unwritable_dir_degrades_without_raising(self, tmp_path: Path) -> None:
        """A sink pointed at an unwritable dir returns from emit, never raises."""
        locked = tmp_path / "locked"
        locked.mkdir()
        locked.chmod(0o500)
        try:
            handler = AppendLogHandler.bind(AtomicAppendLog(locked / "vox.log"))
            handler.emit(_record("m", "degrade"))  # no exception
        finally:
            locked.chmod(0o700)
