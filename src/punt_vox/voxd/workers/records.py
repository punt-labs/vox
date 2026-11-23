"""Private atomic JSON records survive daemon restarts."""

from __future__ import annotations

import json
import logging
from hashlib import sha256
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

    @property
    def namespace(self) -> str:
        """Identify resources belonging to this registry, not another daemon."""
        return sha256(str(self._root.resolve()).encode()).hexdigest()[:16]

    def protects_session(self, worker_id: str) -> bool:
        """Keep active or unreadable checkpoints; terminal workers own no pane."""
        path = self._root / f"{UUID(worker_id)}.json"
        try:
            record = self._read(path)
        except FileNotFoundError:
            return False
        except (OSError, ValueError):
            return True
        return record.state in ("starting", "ready")

    def save(self, record: WorkerRecord) -> None:
        """Commit a full record with owner-only permissions."""
        worker_id = str(UUID(record.id))
        AtomicFile(self._root / f"{worker_id}.json").replace(
            json.dumps(WorkerRecordCodec.encode(record), indent=2) + "\n", mode=0o600
        )

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
