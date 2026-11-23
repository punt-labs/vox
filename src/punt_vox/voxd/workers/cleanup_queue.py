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

    __slots__ = ("_closed", "_lock", "_pending", "_thread")
    _closed: bool
    _lock: Lock
    _pending: deque[tuple[Future[None], Callable[[], None]]]
    _thread: Thread | None  # None when the queue has no consumer.

    def __new__(cls) -> Self:
        """Create a queue with no running thread."""
        self = super().__new__(cls)
        self._closed = False
        self._lock = Lock()
        self._pending = deque()
        self._thread = None
        return self

    def submit(self, operation: Callable[[], None]) -> Future[None]:
        """Start a daemon consumer on demand."""
        job: Future[None] = Future()
        with self._lock:
            if self._closed:
                job.cancel()
                return job
            self._pending.append((job, operation))
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
                job, operation = self._pending.popleft()
            try:
                operation()
            except Exception as exc:
                logger.exception("Worker cleanup job failed")
                job.set_exception(exc)
            else:
                job.set_result(None)

    def shutdown(self) -> None:
        """Discard queued jobs and allow the running attempt a short grace period."""
        with self._lock:
            self._closed = True
            while self._pending:
                job, _ = self._pending.popleft()
                job.cancel()
            thread = self._thread
        if thread is not None:
            thread.join(timeout=_DRAIN_S)
