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


def test_rollover_tightens_a_stale_backup_that_shifts_outward(tmp_path: Path) -> None:
    """A pre-existing 0644 backup at slot >=2 is 0600 after it shifts to slot 3.

    ``doRollover`` shifts ``.2 -> .3`` with a bare ``os.rename`` (only the
    ``base -> .1`` shift runs through ``rotate``), so a backup that predates the
    0600 contract keeps its group/other-readable bits through that rename. The
    ``.2`` slot (within ``backupCount=3``) survives one rollover as ``.3``, so
    the assertion observes the very gap a ``rotate``-only override left open.
    """
    log = tmp_path / "tts.log"
    log.write_text("x" * 200)
    log.chmod(0o644)
    # Stale 0644 backup at slot 2 -- the rename .2 -> .3 never runs through
    # rotate(), so only a doRollover() override tightens it. Slot 3 is the last
    # kept slot at backupCount=3, so it is NOT deleted before the assertion.
    stale = tmp_path / "tts.log.2"
    stale.write_text("old backup")
    stale.chmod(0o644)

    handler = PrivateRotatingFileHandler(str(log), maxBytes=100, backupCount=3)
    try:
        handler.emit(_record("fresh"))  # one write over maxBytes -> one rollover
    finally:
        handler.close()

    shifted = tmp_path / "tts.log.3"
    assert shifted.exists(), "the stale backup must survive the rollover to slot 3"
    assert _mode(shifted) == 0o600, "the shifted-outward backup must be tightened"
    assert _mode(log) == 0o600
    for backup in tmp_path.glob("tts.log.*"):
        assert _mode(backup) == 0o600, backup
