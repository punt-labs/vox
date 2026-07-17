"""A rotating file handler that keeps its log file and backups private (0600)."""

from __future__ import annotations

import contextlib
import io
import logging.handlers
import os
from pathlib import Path
from typing import final

__all__ = ["PrivateRotatingFileHandler"]

_FILE_MODE = 0o600  # private per-user log file


@final
class PrivateRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """Rotating file handler that forces its active file and backups to 0600.

    ``RotatingFileHandler`` sets no mode, so a file created under a permissive
    umask -- or one pre-existing at 0644 -- stays group/other-readable, and every
    rotated backup inherits those bits. This tightens the active file on every
    open (construction and post-rollover reopen) and every backup as it is
    renamed during rollover -- so a pre-existing 0644 backup is tightened the
    moment it shifts.

    Tightening is best-effort: a ``chmod`` we cannot perform must never block the
    log write it protects. The failure is swallowed *silently* -- logging it here
    would re-enter this very handler mid-rollover.
    """

    __slots__ = ()

    def _open(self) -> io.TextIOWrapper:
        """Open the stream and force the freshly-opened file to 0600."""
        stream = super()._open()
        with contextlib.suppress(OSError):
            os.fchmod(stream.fileno(), _FILE_MODE)
        return stream

    def rotate(self, source: str, dest: str) -> None:
        """Rename per the rotation policy, then tighten the new backup to 0600."""
        super().rotate(source, dest)
        with contextlib.suppress(OSError):
            Path(dest).chmod(_FILE_MODE)
