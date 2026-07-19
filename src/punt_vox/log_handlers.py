"""A rotating file handler that keeps its log file and backups private (0600)."""

from __future__ import annotations

import contextlib
import io
import logging.handlers
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Self, final

__all__ = ["PrivateRotatingFileHandler"]

_FILE_MODE = 0o600  # private per-user log file


@final
class PrivateRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """Rotating file handler that forces its active file and backups to 0600.

    ``RotatingFileHandler`` opens ``0666 & ~umask`` -- umask-dependent, never
    guaranteed private -- and never re-tightens. This handler closes both gaps:
    :meth:`_open` creates a new file 0600 *atomically*, and
    :meth:`tighten_existing` re-tightens a file that pre-existed at a laxer mode
    -- the active log and every backup slot -- at startup (via
    :meth:`from_config`) and after every rollover, so a 0644 file left by an
    earlier, laxer run is fixed before it is next rotated, not left loose.

    Tightening is best-effort: a ``chmod`` we cannot perform must never block the
    log write it protects, and reporting the failure through logging here would
    re-enter this very handler. It is swallowed *silently*; a tightening failure
    that matters is surfaced on the startup path, outside the log-write path.
    """

    __slots__ = ()

    @classmethod
    def from_config(
        cls,
        filename: str,
        *,
        maxBytes: int = 0,
        backupCount: int = 0,
        encoding: str | None = None,
    ) -> Self:
        """Build a handler and re-tighten pre-existing files at startup.

        The dictConfig ``"()"`` factory. Construction opens the active file
        (created 0600 by the opener); this then re-tightens the active log and
        every existing backup slot, so a legacy 0644 file left by an earlier,
        laxer run is fixed the first time the handler runs -- not only when it
        next happens to rotate. ``backupCount`` is unset while the parent's
        constructor runs :meth:`_open`, so the whole-chain sweep cannot live in
        the constructor path; it belongs here, once the slots are known.
        """
        handler = cls(
            filename, maxBytes=maxBytes, backupCount=backupCount, encoding=encoding
        )
        handler.tighten_existing()
        return handler

    def _open(self) -> io.TextIOWrapper:
        """Open the stream after ensuring the file exists 0600 atomically.

        Pre-create the target with ``os.open`` + ``O_CREAT`` and mode 0600: the
        kernel applies the mode *as it creates* the file, so a brand-new log
        never exists at the umask default before a follow-up ``chmod`` -- the
        window a plain ``open()`` + ``chmod`` leaves. ``O_CREAT`` without
        ``O_TRUNC`` leaves an existing file's bytes and mode untouched, so a
        pre-existing 0644 log still needs the ``fchmod`` below; that also covers
        the active file on direct construction, not only via
        :meth:`from_config`.
        """
        flags = os.O_CREAT | os.O_WRONLY | os.O_APPEND
        os.close(os.open(self.baseFilename, flags, _FILE_MODE))
        stream = super()._open()
        with contextlib.suppress(OSError):
            os.fchmod(stream.fileno(), _FILE_MODE)
        return stream

    def doRollover(self) -> None:
        """Roll over, then force the active file and every backup slot to 0600."""
        super().doRollover()
        self.tighten_existing()

    def tighten_existing(self) -> None:
        """Force the active file and every existing backup slot to 0600.

        ``RotatingFileHandler.doRollover`` shifts ``.1 -> .2 -> ...`` with a bare
        ``os.rename`` that preserves each source's mode, and the opener ignores
        the mode of a file that already exists -- so neither closes a slot left
        0644 by a laxer earlier run. One sweep over the whole chain does. A
        missing slot raises ``FileNotFoundError`` (an ``OSError``), which the
        suppression skips.
        """
        for path in self._backup_paths():
            with contextlib.suppress(OSError):
                path.chmod(_FILE_MODE)

    def _backup_paths(self) -> Iterator[Path]:
        """Yield the active file and every possible rotated-backup path.

        ``rotation_filename`` honours a custom ``namer``, so a project that
        renames its backups still has the real files tightened, not guessed ones.
        """
        yield Path(self.baseFilename)
        for n in range(1, self.backupCount + 1):
            yield Path(self.rotation_filename(f"{self.baseFilename}.{n}"))
