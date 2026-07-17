"""Tests for the private (0600) rotating log handler."""

from __future__ import annotations

import logging
import stat
from pathlib import Path

from punt_vox.log_handlers import PrivateRotatingFileHandler


def _mode(path: Path) -> int:
    """Return the file's permission bits (low 9)."""
    return stat.S_IMODE(path.stat().st_mode)


def _record(message: str) -> logging.LogRecord:
    """Return an INFO record whose formatted line carries *message*."""
    return logging.LogRecord("t", logging.INFO, __file__, 1, message, None, None)


def test_open_tightens_preexisting_0644_file(tmp_path: Path) -> None:
    """A log file pre-existing at 0644 is forced to 0600 when the handler opens it."""
    log = tmp_path / "tts.log"
    log.write_text("stale line\n")
    log.chmod(0o644)

    handler = PrivateRotatingFileHandler(str(log), maxBytes=10_000, backupCount=3)
    try:
        handler.emit(_record("fresh"))
    finally:
        handler.close()

    assert _mode(log) == 0o600


def test_rollover_tightens_active_and_all_backups(tmp_path: Path) -> None:
    """After rollovers, the active file and every rotated backup are 0600."""
    log = tmp_path / "tts.log"
    log.write_text("x" * 200)
    log.chmod(0o644)
    # A pre-existing backup left at 0644 by an earlier run must also be tightened.
    backup1 = tmp_path / "tts.log.1"
    backup1.write_text("old backup")
    backup1.chmod(0o644)

    handler = PrivateRotatingFileHandler(str(log), maxBytes=100, backupCount=3)
    try:
        for i in range(5):
            handler.emit(_record("y" * 80 + str(i)))
    finally:
        handler.close()

    assert _mode(log) == 0o600
    for backup in tmp_path.glob("tts.log.*"):
        assert _mode(backup) == 0o600, backup
