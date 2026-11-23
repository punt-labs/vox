"""Load and persist the last known result sequence for each worker."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Self, cast, final

from punt_vox.voxd.voice_call.cursor_file import CursorFile

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

logger = logging.getLogger(__name__)


@final
class DepartureCursor:
    """Use a fresh baseline when the saved cursor is absent or unusable."""

    __slots__ = ("_file", "_known")
    _file: CursorFile
    _known: dict[str, int]

    def __new__(cls, path: Path) -> Self:
        """Bind the cursor file for the lifetime of one call."""
        self = super().__new__(cls)
        self._file = CursorFile(path)
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
        for worker_id, sequence in reported.items():
            self._known[worker_id] = max(sequence, self.sequence(worker_id))
        self._file.replace(json.dumps(self._known) + "\n")

    def _read(self) -> dict[str, int] | None:
        try:
            cursor: object = json.loads(self._file.read())
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
