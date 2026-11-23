"""Track durable worker identity independently of worker control."""

from __future__ import annotations

from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, Self, cast, final

from punt_vox.voxd.hook_gaps import HookGapReport, HookGaps

if TYPE_CHECKING:
    from collections.abc import Callable

    from punt_vox.voxd.workers.model import WorkerRecord
    from punt_vox.voxd.workers.records import WorkerRecords


@final
class WorkerInventory:
    """Own the in-process inventory, persistence and hook identity."""

    __slots__ = ("_exited", "_records", "_unconfirmed", "_updates", "_workers")
    _exited: set[str]
    _records: WorkerRecords
    _unconfirmed: set[str]
    _updates: RLock
    _workers: dict[str, WorkerRecord]

    def __new__(cls, records: WorkerRecords) -> Self:
        """Bind the backing records to an initially empty inventory."""
        self = super().__new__(cls)
        self._records = records
        self._workers = {}
        self._exited = set()
        self._unconfirmed = set()
        self._updates = RLock()
        return self

    @property
    def namespace(self) -> str:
        """Identify the ownership scope of a launch."""
        return self._records.namespace

    def worktree(self, worker_id: str) -> Path:
        """Locate a worker's work product."""
        return self._records.worktree(worker_id)

    def restore(
        self,
        records: list[WorkerRecord],
        *,
        unconfirmed: set[str] | None = None,  # PY-TS-14: omit without a probe pass.
    ) -> None:
        """Install the inventory before any RPC is accepted."""
        with self._updates:
            self._workers = {record.id: record for record in records}
            self._unconfirmed = set(unconfirmed or ())

    def confirm(self, worker_id: str) -> None:
        """Admit active hooks after a positive provider observation."""
        with self._updates:
            self._unconfirmed.discard(worker_id)

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

    def update(
        self, worker_id: str, transform: Callable[[WorkerRecord], WorkerRecord]
    ) -> WorkerRecord:
        """Serialize updates that can race with retirement cleanup completion."""
        with self._updates:
            return self.save(transform(self.get(worker_id)))

    def remember(self, record: WorkerRecord) -> None:
        """Retain a record in memory if rollback cannot persist it."""
        with self._updates:
            self._workers[record.id] = record

    def close(self, worker_id: str) -> None:
        """Close active hook admission before the context becomes evictable."""
        with self._updates:
            self._exited.add(worker_id)

    def exited(self, record: WorkerRecord) -> bool:
        """Check whether startup or a later listing observed an exit."""
        return record.state == "exited" or record.id in self._exited

    def match(
        self, worker_id: object, cwd: object, event: object
    ) -> WorkerRecord | None:
        """Match the exact registered worktree and admissible event type."""
        record, _ = self._classify(worker_id, cwd, event)
        return record

    def admission(
        self, worker_id: object, cwd: object, event: object
    ) -> tuple[bool, bool]:
        """Say whether to admit the hook or advise retrying a quarantine denial."""
        record, retryable = self._classify(worker_id, cwd, event)
        return record is not None, retryable

    def _classify(
        self, worker_id: object, cwd: object, event: object
    ) -> tuple[WorkerRecord | None, bool]:
        if not isinstance(worker_id, str) or worker_id not in self._workers:
            return None, False
        worktree = self._hook_worktree(cwd)
        with self._updates:
            record = self._workers.get(worker_id)
            if record is None or record.worktree != worktree:
                return None, False
            return self._admit_record(record, event)

    def _admit_record(
        self, record: WorkerRecord, event: object
    ) -> tuple[WorkerRecord | None, bool]:
        if "launch_error" in record.last_report or record.state == "stopped":
            return None, False
        if event in ("Stop", "SessionEnd"):
            return record, False
        if self.exited(record):
            return None, False
        if record.id in self._unconfirmed and event != "Notification":
            return None, isinstance(event, str)
        return record, False

    @staticmethod
    def _hook_worktree(cwd: object) -> str | None:
        if not isinstance(cwd, str) or not cwd:
            return None
        path = Path(cwd)
        return str(path.resolve()) if path.is_absolute() else None

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
