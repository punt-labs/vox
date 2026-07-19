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
    :meth:`_open` creates a new file 0600 *atomically* (and refuses to follow a
    symlink at the log path), and :meth:`tighten_existing` re-tightens a file
    that pre-existed at a laxer mode -- the active log and every backup slot --
    at startup (via :meth:`from_config`) and after every rollover, so a 0644
    file left by an earlier, laxer run is fixed before it is next rotated.

    A ``chmod`` that fails is *not* swallowed silently: it must never crash the
    log write it protects, and logging it from here would re-enter this very
    handler, so :meth:`tighten_existing` collects the paths it could not tighten
    and exposes them via :attr:`tighten_failures`. The caller that configured
    the handler checks that tuple *after* the handler is live and emits one
    ``WARNING`` naming the still-loose files -- durable in the now-open log,
    greppable, and outside the recursive log-write path.
    """

    __slots__ = ("_tighten_failures",)

    _tighten_failures: tuple[Path, ...]

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
        (created 0600 atomically by :meth:`_open`); this then re-tightens the
        active log and every backup slot, so a legacy 0644 file left by an earlier,
        laxer run is fixed the first time the handler runs -- not only when it
        next happens to rotate. ``backupCount`` is unset while the parent's
        constructor runs :meth:`_open`, so the whole-chain sweep cannot live in
        the constructor path; it belongs here, once the slots are known. The
        sweep records any un-tightenable path in :attr:`tighten_failures` for the
        caller to surface.
        """
        handler = cls(
            filename, maxBytes=maxBytes, backupCount=backupCount, encoding=encoding
        )
        handler.tighten_existing()
        return handler

    @property
    def tighten_failures(self) -> tuple[Path, ...]:
        """Return the paths the last sweep could not chmod to 0600.

        Empty when every path was tightened. The caller that configured the
        handler emits a ``WARNING`` naming these once the log is live.
        """
        return self._tighten_failures

    def _open(self) -> io.TextIOWrapper:
        """Open the stream after ensuring the file exists 0600 atomically.

        Pre-create the target with ``os.open`` + ``O_CREAT`` and mode 0600: the
        kernel applies the mode *as it creates* the file, so a brand-new log
        never exists at the umask default before a follow-up ``chmod`` -- the
        window a plain ``open()`` + ``chmod`` leaves. ``O_NOFOLLOW`` refuses a
        symlink at the log path (never legitimate; fail loud, not through it).
        ``O_CREAT`` without ``O_TRUNC`` leaves an existing file's bytes and mode
        untouched, so a pre-existing 0644 log still needs the ``fchmod`` below;
        that also covers the active file on direct construction.
        """
        self._tighten_failures = ()
        flags = os.O_CREAT | os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW
        os.close(os.open(self.baseFilename, flags, _FILE_MODE))
        stream = super()._open()
        with contextlib.suppress(OSError):
            os.fchmod(stream.fileno(), _FILE_MODE)
        return stream

    def doRollover(self) -> None:
        """Roll over, then force the active file and every backup slot to 0600."""
        super().doRollover()
        self.tighten_existing()

    def tighten_existing(self) -> tuple[Path, ...]:
        """Force the active file and every existing backup slot to 0600.

        ``RotatingFileHandler.doRollover`` shifts ``.1 -> .2 -> ...`` with a bare
        ``os.rename`` that preserves each source's mode, and the opener ignores
        the mode of a file that already exists -- so neither closes a slot left
        0644 by a laxer earlier run. One sweep over the whole chain does.

        A missing backup slot is skipped -- it is not loose. A ``chmod`` that
        genuinely fails (permission, ownership) is collected and returned (and
        stored on :attr:`tighten_failures`) rather than raised: this runs inside
        the handler and must never crash logging, but the failure is not lost --
        the caller surfaces it as a ``WARNING``.
        """
        failures: list[Path] = []
        for path in self._backup_paths():
            if not path.exists():
                continue
            try:
                path.chmod(_FILE_MODE)
            except OSError:
                failures.append(path)
        self._tighten_failures = tuple(failures)
        return self._tighten_failures

    def _backup_paths(self) -> Iterator[Path]:
        """Yield the active file and every possible rotated-backup path.

        ``rotation_filename`` honours a custom ``namer``, so a project that
        renames its backups still has the real files tightened, not guessed ones.
        """
        yield Path(self.baseFilename)
        for n in range(1, self.backupCount + 1):
            yield Path(self.rotation_filename(f"{self.baseFilename}.{n}"))
