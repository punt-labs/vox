"""Run serialized cleanup with a bounded daemon shutdown."""

from __future__ import annotations

import logging
from collections import deque
from concurrent.futures import Future
from threading import Lock, Thread
from typing import TYPE_CHECKING, Self, final

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)
_DRAIN_S = 0.25


@final
class WorkerCleanupQueue:
    """Cancel unstarted work; an interrupted attempt remains pending on disk.

    One job per worker is active at a time. A request for a worker whose job
    is still running is declined, since that job settles every debt marked
    while it runs; but the queue remembers the request and runs the worker's
    job once more when the active one ends. A request that lands between the
    job's last check and the worker's release is therefore never dropped.
    """

    __slots__ = ("_active", "_closed", "_lock", "_pending", "_rerun", "_thread")
    _active: set[str]
    _closed: bool
    _lock: Lock
    _pending: deque[tuple[str, Future[None], Callable[[], None]]]
    _rerun: dict[str, Callable[[], None]]
    _thread: Thread | None  # None when the queue has no consumer.

    def __new__(cls) -> Self:
        """Create a queue with no running thread."""
        self = super().__new__(cls)
        self._active = set()
        self._closed = False
        self._lock = Lock()
        self._pending = deque()
        self._rerun = {}
        self._thread = None
        return self

    def is_idle(self) -> bool:
        """Check whether no cleanup job is queued or running."""
        with self._lock:
            return not self._active

    def submit(
        self, worker_id: str, operation: Callable[[], None]
    ) -> Future[None] | None:  # PY-TS-14: duplicate or closed queues decline work.
        """Schedule one job per worker and start a daemon consumer on demand.

        A declined request for an active worker is run once that job ends.
        """
        with self._lock:
            if self._closed:
                return None
            if worker_id in self._active:
                self._rerun[worker_id] = operation
                return None
            job: Future[None] = Future()
            self._active.add(worker_id)
            self._pending.append((worker_id, job, operation))
            self._start_consumer()
        return job

    def _start_consumer(self) -> None:
        """Start the daemon consumer when none is running; the lock is held."""
        if self._thread is None:
            self._thread = Thread(target=self._consume, name="vox-cleanup", daemon=True)
            self._thread.start()

    def _consume(self) -> None:
        while True:
            with self._lock:
                if not self._pending:
                    self._thread = None
                    return
                worker_id, job, operation = self._pending.popleft()
            self._run(worker_id, job, operation)

    def _run(
        self, worker_id: str, job: Future[None], operation: Callable[[], None]
    ) -> None:
        failure = None
        try:
            operation()
        except Exception as exc:
            logger.exception("Worker %s cleanup job failed", worker_id)
            failure = exc
        finally:
            with self._lock:
                self._release_or_follow_up(worker_id)
        if failure is None:
            job.set_result(None)
        else:
            job.set_exception(failure)

    def _release_or_follow_up(self, worker_id: str) -> None:
        """Rerun a request declined during the job, or release the worker.

        The lock is held. The rerun keeps the worker active, so idleness
        still means every requested job has ended.
        """
        operation = self._rerun.pop(worker_id, None)
        if operation is None or self._closed:
            self._active.remove(worker_id)
            return
        self._pending.appendleft((worker_id, Future(), operation))

    def shutdown(self) -> None:
        """Discard queued jobs and allow the running attempt a short grace period."""
        with self._lock:
            self._closed = True
            while self._pending:
                worker_id, job, _ = self._pending.popleft()
                self._active.remove(worker_id)
                job.cancel()
            self._rerun.clear()
            thread = self._thread
        if thread is not None:
            thread.join(timeout=_DRAIN_S)
