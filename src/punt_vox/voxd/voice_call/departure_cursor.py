"""Load and persist the last known result sequence for each worker."""

from __future__ import annotations

import json
import logging
import os
import stat
from contextlib import suppress
from typing import TYPE_CHECKING, Self, cast, final

from punt_vox.atomic_file import AtomicFile

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

logger = logging.getLogger(__name__)


@final
class DepartureCursor:
    """Use a fresh baseline when the saved cursor is absent or unusable."""

    __slots__ = ("_file", "_known")
    _file: AtomicFile
    _known: dict[str, int]

    def __new__(cls, path: Path) -> Self:
        """Bind the cursor file for the lifetime of one call."""
        self = super().__new__(cls)
        self._file = AtomicFile(path)
        self._known = {}
        return self

    def load(self, baseline: Iterable[tuple[str, int]]) -> bool:
        """Load the cursor or seed it; return whether an earlier call saved it."""
        cursor = self._read()
        self._known = dict(baseline) if cursor is None else cursor
        return cursor is not None

    def sequence(self, worker_id: str) -> int:
        """Return the highest known result sequence for the worker."""
        return self._known.get(worker_id, 0)

    def record(self, worker_id: str, sequence: int) -> None:
        """Advance the boundary for a result included in the opener."""
        if sequence > self.sequence(worker_id):
            self._known[worker_id] = sequence

    def save(self, reported: dict[str, int]) -> None:
        """Include results submitted for speech and atomically save the cursor."""
        with suppress(FileNotFoundError):
            self._require_regular(self._file.path.stat().st_mode)
        for worker_id, sequence in reported.items():
            self._known[worker_id] = max(sequence, self.sequence(worker_id))
        self._file.replace(json.dumps(self._known) + "\n", mode=0o600)

    @staticmethod
    def _require_regular(mode: int) -> None:
        if not stat.S_ISREG(mode):
            raise ValueError("Call departure cursor is not a regular file")

    def _read_text(self) -> str:
        path = self._file.path
        self._require_regular(path.stat().st_mode)
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        with os.fdopen(fd, encoding="utf-8") as stream:
            self._require_regular(os.fstat(stream.fileno()).st_mode)
            return stream.read()

    def _read(self) -> dict[str, int] | None:
        try:
            cursor: object = json.loads(self._read_text())
            if isinstance(cursor, dict) and all(
                type(seq) is int and seq >= 0
                for seq in cast("dict[str, object]", cursor).values()
            ):
                return cast("dict[str, int]", cursor)
        except FileNotFoundError:
            return None
        except (OSError, ValueError, RecursionError):
            pass
        logger.warning("Ignoring invalid call departure cursor")
        return None
