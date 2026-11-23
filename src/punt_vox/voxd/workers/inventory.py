"""Track durable worker identity independently of worker control."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Self, cast, final

from punt_vox.voxd.hook_gaps import HookGapReport, HookGaps

if TYPE_CHECKING:
    from punt_vox.voxd.workers.model import WorkerRecord
    from punt_vox.voxd.workers.records import WorkerRecords


@final
class WorkerInventory:
    """Own the in-process inventory, persistence and hook identity."""

    __slots__ = ("_exited", "_records", "_workers")
    _exited: set[str]
    _records: WorkerRecords
    _workers: dict[str, WorkerRecord]

    def __new__(cls, records: WorkerRecords) -> Self:
        """Bind the backing records to an initially empty inventory."""
        self = super().__new__(cls)
        self._records = records
        self._workers = {}
        self._exited = set()
        return self

    @property
    def namespace(self) -> str:
        """Identify the ownership scope of a launch."""
        return self._records.namespace

    def worktree(self, worker_id: str) -> Path:
        """Locate a worker's work product."""
        return self._records.worktree(worker_id)

    def restore(self, records: list[WorkerRecord]) -> None:
        """Install the inventory before any RPC is accepted."""
        self._workers = {record.id: record for record in records}

    def registered(self) -> list[WorkerRecord]:
        """Return a snapshot of the registered workers."""
        return list(self._workers.values())

    def get(self, worker_id: str) -> WorkerRecord:
        """Find the record or report an unknown worker."""
        try:
            return self._workers[worker_id]
        except KeyError:
            raise ValueError(f"no worker {worker_id}") from None

    def save(self, record: WorkerRecord) -> WorkerRecord:
        """Persist a changed record before exposing it to other operations."""
        if self._workers.get(record.id) != record:
            self._records.save(record)
            self.remember(record)
        return record

    def remember(self, record: WorkerRecord) -> None:
        """Retain a record in memory if rollback cannot persist it."""
        self._workers[record.id] = record

    def close(self, worker_id: str) -> None:
        """Close active hook admission before the context becomes evictable."""
        self._exited.add(worker_id)

    def exited(self, record: WorkerRecord) -> bool:
        """Check whether startup or a later listing observed an exit."""
        return record.state == "exited" or record.id in self._exited

    def match(
        self, worker_id: object, cwd: object, event: object
    ) -> WorkerRecord | None:
        """Match the exact registered worktree and admissible event type."""
        if not isinstance(worker_id, str) or not isinstance(cwd, str) or not cwd:
            return None
        record = self._workers.get(worker_id)
        if record is None or "launch_error" in record.last_report:
            return None
        if record.state == "stopped":
            return None
        if self.exited(record) and event not in ("Stop", "SessionEnd"):
            return None
        if Path(cwd).is_absolute() and record.worktree == str(Path(cwd).resolve()):
            return record
        return None

    @staticmethod
    def received(record: WorkerRecord, sequence: int) -> bool:
        """Recognize an event already saved in the durable gap checkpoint."""
        report = record.last_report
        gaps = report.get("gaps")
        return (
            "worker_seq" in report
            and isinstance(gaps, dict)
            and HookGaps.received(cast("HookGapReport", gaps), sequence)
        )
