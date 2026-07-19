"""A multi-writer-safe append-only log sink: atomic ``O_APPEND``, 0600, rotation.

Generalizes the ``vibe_trace`` line-writer. Every :meth:`AtomicAppendLog.append`
escapes its text to exactly one physical line and writes it in a *single*
``os.write`` under ``O_APPEND``. POSIX guarantees an ``O_APPEND`` write seeks to
end and appends atomically, and a log line is far shorter than the platform's
atomic-append size, so lines from concurrent processes never interleave -- no
lock, no long-lived shared handle. Many writers therefore share one file safely.

The error path must never re-enter the logging subsystem: this sink is the
fallback for records that could not be shipped through ``logging``, so calling
``logger.*`` on an ``OSError`` would recurse back into the client log handler.
On failure it writes a best-effort note to ``sys.__stderr__`` and returns.

Rotation is best-effort rename-on-oversize (Open Decision D4): before an append
that would cross ``max_bytes`` the chain shifts ``.N-1 -> .N``. A rare
double-rename race under concurrent writers is bounded and never for these
low-volume sinks (``vibe-trace.log`` reached 62 KB over the project's life); a
lossless rotate-and-signal scheme is overkill for sinks whose writers hold no
long-lived descriptor.
"""

from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path
from typing import Self, final

from punt_vox.log_sanitize import SANITIZER
from punt_vox.private_state import PrivateState

__all__ = ["AtomicAppendLog"]

_OPEN_FLAGS = os.O_WRONLY | os.O_APPEND | os.O_CREAT
_MAX_BYTES = 5_242_880  # 5 MB
_BACKUP_COUNT = 5


@final
class AtomicAppendLog:
    """An append-only sink safe for many concurrent writers to one file."""

    __slots__ = ("_backup_count", "_guard", "_max_bytes", "_path")

    _path: Path
    _guard: PrivateState
    _max_bytes: int
    _backup_count: int

    def __new__(
        cls,
        path: Path,
        *,
        max_bytes: int = _MAX_BYTES,
        backup_count: int = _BACKUP_COUNT,
    ) -> Self:
        self = super().__new__(cls)
        self._path = path
        self._guard = PrivateState(path)
        self._max_bytes = max_bytes
        self._backup_count = backup_count
        return self

    @property
    def path(self) -> Path:
        """Return the file this sink appends to."""
        return self._path

    def append(self, text: str) -> None:
        """Escape *text* to one line and append it atomically; never raise, never log.

        *text* is escaped by the shared :data:`SANITIZER` so the record is exactly
        one physical line no control byte can forge or corrupt -- callers pass raw
        content and trust the sink to keep it one line. A short write (only
        ``ENOSPC`` in practice, since Python auto-retries ``EINTR``) is *not*
        looped: re-issuing the remainder would be a second append another writer's
        line could split, tearing the very line ``O_APPEND`` protects. On any
        ``OSError`` the sink writes to ``sys.__stderr__`` -- never through
        ``logging``, which would recurse into the client log handler this sink
        backstops.
        """
        line = f"{SANITIZER.escape(text)}\n".encode()
        try:
            self._guard.ensure_private_tree()
            self._rotate_if_needed(len(line))
            fd = self._guard.open_private(_OPEN_FLAGS)
            try:
                if (written := os.write(fd, line)) != len(line):
                    self._to_stderr(
                        f"short append to {self._path}: {written} of {len(line)} bytes"
                    )
            finally:
                os.close(fd)
        except OSError as exc:
            self._to_stderr(f"cannot append to {self._path}: {exc}")

    def is_writable(self) -> bool:
        """Return whether a line could be appended right now; never raise.

        Health is a property of the *path*, not of any one process's last write:
        separate processes append here, so trusting a single writer's success
        would report a peer's state. An existing log must be a writable regular
        file; a not-yet-created log needs its nearest existing ancestor to grant
        both write and search (``W_OK | X_OK``). Any :class:`OSError` from probing
        the real filesystem is fail-safe (report ``False``) so a health check can
        never crash the surface that reports it.
        """
        try:
            if self._path.exists():
                return self._path.is_file() and os.access(self._path, os.W_OK)
            anchor = self._guard.nearest_existing_ancestor()
            return anchor.is_dir() and os.access(anchor, os.W_OK | os.X_OK)
        except OSError:
            return False

    def _rotate_if_needed(self, incoming: int) -> None:
        """Shift ``.N-1 -> .N`` when the next append would cross ``max_bytes``."""
        if self._max_bytes <= 0:
            return
        try:
            size = self._path.stat().st_size
        except OSError:
            return
        if size + incoming <= self._max_bytes:
            return
        self._rotate()

    def _rotate(self) -> None:
        """Best-effort rename chain; a failure leaves the sink appending in place."""
        for n in range(self._backup_count, 0, -1):
            src = (
                self._path
                if n == 1
                else self._path.with_name(f"{self._path.name}.{n - 1}")
            )
            dst = self._path.with_name(f"{self._path.name}.{n}")
            try:
                if src.exists():
                    src.replace(dst)
            except OSError:
                return

    @staticmethod
    def _to_stderr(message: str) -> None:
        """Write a best-effort note to the real stderr; swallow if unavailable."""
        err = sys.__stderr__
        if err is None:
            return
        with contextlib.suppress(OSError, ValueError):
            err.write(f"[append-log] {message}\n")
