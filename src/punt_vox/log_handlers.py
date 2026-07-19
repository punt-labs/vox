"""A rotating file handler that keeps its log file and backups private (0600)."""

from __future__ import annotations

import contextlib
import io
import logging.handlers
import os
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import Self, cast, final

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

        Empty when every path was tightened -- and, via the ``getattr`` default,
        before the first sweep runs (e.g. ``delay=True``, where ``_open`` has not
        yet fired), so reading this never raises. The caller that configured the
        handler emits a ``WARNING`` naming these once the log is live.
        """
        return getattr(self, "_tighten_failures", ())

    def _open(self) -> io.TextIOWrapper:
        """Open the log through a single ``O_NOFOLLOW`` fd and write through it.

        Open *once* with ``os.open`` and build the stream from that fd with
        ``os.fdopen``: creation, no-follow validation, permissions, and every
        write then operate on the same inode. There is no second, symlink-
        following ``open()`` for an attacker to race between (the pre-create +
        reopen shape that TOCTOU defeats). ``O_CREAT | 0600`` makes a brand-new
        log private at creation -- no umask-default window -- and ``O_NOFOLLOW``
        refuses a symlink at the log path (never legitimate; fail loud, not
        through it). ``O_CREAT`` without ``O_TRUNC`` keeps an existing file's
        bytes and mode, so the ``fchmod`` re-tightens a pre-existing 0644 log;
        that failing is best-effort here -- the follow-up ``tighten_existing``
        (run by ``from_config`` and ``doRollover``) is the authoritative record
        that surfaces a genuine failure -- so it is suppressed, not raised.
        """
        self._tighten_failures = ()
        flags = os.O_CREAT | os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW
        fd = os.open(self.baseFilename, flags, _FILE_MODE)
        try:
            with contextlib.suppress(OSError):
                os.fchmod(fd, _FILE_MODE)
            # os.fdopen in text mode yields a TextIOWrapper; the stub says IO[Any].
            stream = os.fdopen(
                fd, self.mode, encoding=self.encoding, errors=self.errors
            )
            return cast("io.TextIOWrapper", stream)
        except BaseException:
            os.close(fd)
            raise

    def doRollover(self) -> None:
        """Roll over, then force the active file and every backup slot to 0600."""
        super().doRollover()
        self.tighten_existing()

    def tighten_existing(self) -> tuple[Path, ...]:
        """Force the active file and every existing backup slot to 0600.

        ``RotatingFileHandler.doRollover`` shifts ``.1 -> .2 -> ...`` with a bare
        ``os.rename`` that preserves each source's mode, so a slot left 0644 by a
        laxer earlier run keeps those bits. One sweep over the whole chain fixes
        them. Record (and store on :attr:`tighten_failures`) the paths that could
        not be tightened, so the caller surfaces them.
        """
        # _tighten does the chmod and reports success; keep the paths it could
        # not tighten -- the failures the caller warns about.
        self._tighten_failures = tuple(
            path for path in self._backup_paths() if not self._tighten(path)
        )
        return self._tighten_failures

    @staticmethod
    def _tighten(path: Path) -> bool:
        """Return whether *path* is now a private (0600) regular file.

        Open the slot with ``O_NOFOLLOW`` and chmod *that fd* -- never the path.
        A path-based ``exists()`` + ``chmod`` follows a symlink and races the
        check against the act, so a ``*.log.N -> victim`` link would chmod the
        victim to 0600 under the daemon's authority. Operating on the fd, and
        refusing a non-regular file via ``fstat``, closes both holes.

        A vanished/absent slot (``ENOENT``) is benign -- the file is gone, not
        left readable -- so it is skipped (reported tightened). A symlink
        (``ELOOP``), a non-regular entry, or a genuine ``chmod`` refusal
        (``EPERM``/``EACCES``) is a real failure and reported as such.
        """
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return True
        except OSError:
            return False
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return False
            os.fchmod(fd, _FILE_MODE)
        except OSError:
            return False
        finally:
            os.close(fd)
        return True

    def _backup_paths(self) -> Iterator[Path]:
        """Yield the active file and every possible rotated-backup path.

        ``rotation_filename`` honours a custom ``namer``, so a project that
        renames its backups still has the real files tightened, not guessed ones.
        """
        yield Path(self.baseFilename)
        for n in range(1, self.backupCount + 1):
            yield Path(self.rotation_filename(f"{self.baseFilename}.{n}"))
