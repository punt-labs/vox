"""Private atomic JSON records survive daemon restarts."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Self, final
from uuid import UUID

from punt_vox.atomic_file import AtomicFile
from punt_vox.voxd.workers.codec import WorkerRecordCodec
from punt_vox.voxd.workers.model import WorkerRecord

logger = logging.getLogger(__name__)


@final
class WorkerRecords:
    """Own the private worker state directory and validate record identities."""

    __slots__ = ("_root",)
    _root: Path

    def __new__(cls, root: Path) -> Self:
        """Create a private persistent record directory."""
        self = super().__new__(cls)
        self._root = root
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        root.chmod(0o700)
        return self

    def worktree(self, worker_id: str) -> Path:
        """Return a worker's dedicated work product directory."""
        return self._root / str(UUID(worker_id)) / "worktree"

    def save(self, record: WorkerRecord) -> None:
        """Commit a full record with owner-only permissions."""
        worker_id = str(UUID(record.id))
        AtomicFile(self._root / f"{worker_id}.json").replace(
            json.dumps(WorkerRecordCodec.encode(record), indent=2) + "\n", mode=0o600
        )

    def owns_session(self, session_id: object, cwd: Path) -> bool:
        """Admit hooks only for a live registered session in its exact worktree."""
        if not isinstance(session_id, str):
            return False
        try:
            worker_id = str(UUID(session_id))
            record = WorkerRecordCodec.decode(
                json.loads((self._root / f"{worker_id}.json").read_text())
            )
            return (
                record.id == worker_id
                and record.state in ("starting", "ready")
                and record.worktree == str(cwd.resolve())
            )
        except (OSError, ValueError):
            return False

    @staticmethod
    def _read(path: Path) -> WorkerRecord:
        record = WorkerRecordCodec.decode(json.loads(path.read_text()))
        if str(UUID(record.id)) != path.stem:
            raise ValueError(f"Invalid worker record: {path}")
        return record

    def load(self, *, strict: bool = True) -> list[WorkerRecord]:
        """Retain malformed records; optionally isolate them during daemon boot."""
        records: list[WorkerRecord] = []
        for path in sorted(self._root.glob("*.json")):
            try:
                records.append(self._read(path))
            except (OSError, ValueError) as exc:
                if strict:
                    raise
                logger.warning(
                    "Invalid worker record %s (%s)", path.name, type(exc).__name__
                )
        return records
