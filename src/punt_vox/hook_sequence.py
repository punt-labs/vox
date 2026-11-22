"""Sender-side per-session sequence, shared by concurrent hook processes."""

from __future__ import annotations

import fcntl
import hashlib
from typing import TYPE_CHECKING, Self, final

from punt_vox.atomic_file import AtomicFile
from punt_vox.hook_context_event import MAX_SEQUENCE

if TYPE_CHECKING:
    from pathlib import Path


@final
class HookSequence:
    """Advance a flock-protected counter before attempting delivery."""

    __slots__ = ("_path",)
    _path: Path

    def __new__(cls, directory: Path, session: str) -> Self:
        """Use a collision-resistant filename that cannot escape the directory."""
        self = super().__new__(cls)
        digest = hashlib.sha256(session.encode()).hexdigest()
        self._path = directory / f"{digest}.seq"
        return self

    def next(self) -> int:
        """Return the next sequence only after the counter has been fsynced."""
        self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self._path.with_suffix(".lock").open("ab") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            previous = self._read()
            if not 0 <= previous < MAX_SEQUENCE:
                msg = "invalid hook counter; start a new session instead of resuming"
                raise ValueError(msg)
            value = previous + 1
            AtomicFile(self._path).replace(str(value), mode=0o600)
            return value

    def _read(self) -> int:
        try:
            raw = self._path.read_text(encoding="ascii", errors="replace")
        except FileNotFoundError:
            return 0
        if not raw.isdecimal() or len(raw) > 19:
            msg = "corrupt hook counter; start a new session instead of resuming"
            raise ValueError(msg)
        return int(raw)
