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
    """Cancel unstarted work; an interrupted attempt remains pending on disk."""

    __slots__ = ("_active", "_closed", "_lock", "_pending", "_thread")
    _active: set[str]
    _closed: bool
    _lock: Lock
    _pending: deque[tuple[str, Future[None], Callable[[], None]]]
    _thread: Thread | None  # None when the queue has no consumer.

    def __new__(cls) -> Self:
        """Create a queue with no running thread."""
        self = super().__new__(cls)
        self._active = set()
        self._closed = False
        self._lock = Lock()
        self._pending = deque()
        self._thread = None
        return self

    def submit(
        self, worker_id: str, operation: Callable[[], None]
    ) -> Future[None] | None:  # PY-TS-14: duplicate or closed queues decline work.
        """Schedule one job per worker and start a daemon consumer on demand."""
        with self._lock:
            if self._closed or worker_id in self._active:
                return None
            job: Future[None] = Future()
            self._active.add(worker_id)
            self._pending.append((worker_id, job, operation))
            if self._thread is None:
                self._thread = Thread(
                    target=self._consume, name="vox-cleanup", daemon=True
                )
                self._thread.start()
        return job

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
            logger.exception("Worker cleanup job failed")
            failure = exc
        finally:
            with self._lock:
                self._active.remove(worker_id)
        if failure is None:
            job.set_result(None)
        else:
            job.set_exception(failure)

    def shutdown(self) -> None:
        """Discard queued jobs and allow the running attempt a short grace period."""
        with self._lock:
            self._closed = True
            while self._pending:
                worker_id, job, _ = self._pending.popleft()
                self._active.remove(worker_id)
                job.cancel()
            thread = self._thread
        if thread is not None:
            thread.join(timeout=_DRAIN_S)
