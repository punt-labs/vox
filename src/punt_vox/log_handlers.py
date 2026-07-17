"""A rotating file handler that keeps its log file and backups private (0600)."""

from __future__ import annotations

import contextlib
import io
import logging.handlers
import os
from collections.abc import Iterator
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
    open (construction and post-rollover reopen) and, on every rollover, forces
    the active file *and every backup slot* to 0600.

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

    def doRollover(self) -> None:
        """Roll over, then force the active file and every backup slot to 0600.

        ``RotatingFileHandler.doRollover`` runs :meth:`rotate` only for the
        ``base -> base.1`` shift; the ``base.1 -> base.2 -> ...`` shifts use a
        bare ``os.rename`` that preserves each source's mode. A backup left at
        0644 by a laxer earlier run would keep those bits as it moves outward.
        Overriding here -- not ``rotate`` -- lets one pass tighten the whole
        chain so the 0600 guarantee holds for every slot, not just ``.1``.
        """
        super().doRollover()
        for path in self._backup_paths():
            with contextlib.suppress(OSError):
                path.chmod(_FILE_MODE)

    def _backup_paths(self) -> Iterator[Path]:
        """Yield the active file and every possible rotated-backup path."""
        base = Path(self.baseFilename)
        yield base
        for n in range(1, self.backupCount + 1):
            yield base.with_name(f"{base.name}.{n}")
