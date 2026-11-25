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

    __slots__ = (
        "_exited",
        "_records",
        "_unconfirmed",
        "_unsaved",
        "_updates",
        "_workers",
    )
    _exited: set[str]
    _records: WorkerRecords
    _unconfirmed: set[str]
    # Workers whose in-memory record is newer than the disk: a save failed.
    _unsaved: set[str]
    _updates: RLock
    _workers: dict[str, WorkerRecord]

    def __new__(cls, records: WorkerRecords) -> Self:
        """Bind the backing records to an initially empty inventory."""
        self = super().__new__(cls)
        self._records = records
        self._workers = {}
        self._exited = set()
        self._unconfirmed = set()
        self._unsaved = set()
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
        """Persist a changed record before exposing it to other operations.

        Memory may already hold *record* because an earlier save of it
        failed and the failure path remembered it; the disk is then behind
        memory, and the write must happen even though nothing in memory
        changes. Otherwise a retried checkpoint reports success while the
        disk lacks it and a restart loses it.
        """
        with self._updates:
            if self._workers.get(record.id) != record or record.id in self._unsaved:
                self._persist(record)
        return record

    def _persist(self, record: WorkerRecord) -> None:
        """Write the record and mark memory and disk as agreeing; lock held.

        A failed write of a record already registered leaves memory newer
        than the disk: the record stays in memory and its id is marked
        unsaved, so the next save writes even when nothing in memory
        changes. A record whose first save fails was never registered and
        leaves nothing behind.
        """
        try:
            self._records.save(record)
        except (OSError, ValueError):
            if record.id in self._workers:
                self._unsaved.add(record.id)
                self.remember(record)
            raise
        self._unsaved.discard(record.id)
        self.remember(record)

    def acknowledged(self, record: WorkerRecord, sequence: int) -> bool:
        """Say whether a redelivered event is already safe to acknowledge.

        The durable gap checkpoint shows which sequences were saved. Memory
        may show one received while the disk does not, because that save
        failed; such a delivery is projected again so the retried save lands
        it. Only a sequence the disk agrees on is acknowledged from memory.
        """
        gaps = record.last_report.get("gaps")
        with self._updates:
            return (
                "worker_seq" in record.last_report
                and isinstance(gaps, dict)
                and HookGaps.received(cast("HookGapReport", gaps), sequence)
                and record.id not in self._unsaved
            )

    def update(
        self, worker_id: str, transform: Callable[[WorkerRecord], WorkerRecord]
    ) -> WorkerRecord:
        """Serialize updates that can race with retirement cleanup completion.

        The read, the transform and the write happen under one lock, so a
        concurrent update is never overwritten by a stale copy. A save that
        fails still leaves the transformed record in memory, marked unsaved,
        before the error propagates: an outcome such as a finished discard is
        irreversible, and memory must carry it even when the disk does not.
        """
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
