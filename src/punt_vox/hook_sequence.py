"""Sender-side per-session sequence, shared by concurrent hook processes."""

from __future__ import annotations

import fcntl
import hashlib
import os
from typing import TYPE_CHECKING, Self, final

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
        with self._path.open("a+", encoding="ascii") as counter:
            fcntl.flock(counter, fcntl.LOCK_EX)
            counter.seek(0)
            raw = counter.read()
            if raw and (not raw.isdecimal() or len(raw) > 19):
                msg = "corrupt hook counter"
                raise ValueError(msg)
            previous = int(raw) if raw else 0
            if not 0 <= previous < MAX_SEQUENCE:
                msg = "invalid hook counter value"
                raise ValueError(msg)
            value = previous + 1
            counter.seek(0)
            counter.truncate()
            counter.write(str(value))
            counter.flush()
            os.fsync(counter.fileno())
            return value
