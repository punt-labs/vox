"""Tests for the private (0600) rotating log handler."""

from __future__ import annotations

import logging
import os
import stat
from pathlib import Path

from punt_vox.log_handlers import PrivateRotatingFileHandler


def _mode(path: Path) -> int:
    """Return the file's permission bits (low 9)."""
    return stat.S_IMODE(path.stat().st_mode)


def _record(message: str) -> logging.LogRecord:
    """Return an INFO record whose formatted line carries *message*."""
    return logging.LogRecord("t", logging.INFO, __file__, 1, message, None, None)


def test_new_file_is_created_0600_atomically(tmp_path: Path) -> None:
    """A brand-new log is 0600 the moment it exists -- no umask-default window.

    The handler pre-creates the file with ``os.open`` + ``O_CREAT`` and mode
    0600, so even under a permissive umask the file never appears group/other
    readable. Force umask 0 (the worst case: a plain ``open`` would yield 0666)
    to prove the mode comes from the create call, not the ambient umask.
    """
    log = tmp_path / "tts.log"
    old_umask = os.umask(0)
    try:
        handler = PrivateRotatingFileHandler(str(log), maxBytes=10_000, backupCount=3)
        try:
            handler.emit(_record("first line"))
        finally:
            handler.close()
    finally:
        os.umask(old_umask)

    assert log.exists()
    assert _mode(log) == 0o600


def test_from_config_tightens_preexisting_backup_at_startup(tmp_path: Path) -> None:
    """The dictConfig factory re-tightens a stale 0644 backup on construction.

    A backup slot left 0644 by an earlier, laxer run that never rotates would
    stay loose forever under plain construction (``doRollover`` only fires on
    rotation). ``from_config`` sweeps the whole chain at startup, so the legacy
    backup is fixed the first time the handler runs, without any rotation.
    """
    log = tmp_path / "tts.log"
    log.write_text("active\n")
    log.chmod(0o644)
    stale_backup = tmp_path / "tts.log.2"
    stale_backup.write_text("old backup\n")
    stale_backup.chmod(0o644)

    handler = PrivateRotatingFileHandler.from_config(
        str(log), maxBytes=1_000_000, backupCount=5, encoding="utf-8"
    )
    handler.close()

    assert _mode(log) == 0o600, "active file tightened at startup"
    assert _mode(stale_backup) == 0o600, "legacy backup tightened at startup"


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
