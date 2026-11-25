"""Run each worker's cleanup on its own lane, with a bounded daemon shutdown."""

from __future__ import annotations

import logging
import time
from concurrent.futures import Future
from threading import Lock, Thread
from typing import TYPE_CHECKING, Self, final

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)
_DRAIN_S = 0.25


@final
class WorkerCleanupQueue:
    """One cleanup lane per worker; an interrupted attempt remains pending on disk.

    Each worker's job runs on its own thread, so one worker's discard (up to
    six git calls, each under a deadline) never holds another worker's
    harness cleanup, and a stop reply waiting on that cleanup stays inside
    its budget. One job per worker is active at a time. A request for a
    worker whose job is still running is declined, since that job settles
    every debt marked while it runs; but the lane remembers the request and
    runs the worker's job once more when the active one ends, so a request
    that lands between the job's last check and the worker's release is
    never dropped.
    """

    __slots__ = ("_active", "_closed", "_lock", "_rerun", "_threads")
    _active: set[str]
    _closed: bool
    _lock: Lock
    _rerun: dict[str, Callable[[], None]]
    _threads: dict[str, Thread]

    def __new__(cls) -> Self:
        """Create a queue with no running lane."""
        self = super().__new__(cls)
        self._active = set()
        self._closed = False
        self._lock = Lock()
        self._rerun = {}
        self._threads = {}
        return self

    def is_idle(self) -> bool:
        """Check whether no cleanup job is running or waiting to rerun."""
        with self._lock:
            return not self._active

    def submit(
        self, worker_id: str, operation: Callable[[], None]
    ) -> Future[None] | None:  # PY-TS-14: duplicate or closed queues decline work.
        """Run one job per worker on that worker's own lane.

        A declined request for an active worker is run once that job ends.
        """
        with self._lock:
            if self._closed:
                return None
            if worker_id in self._active:
                self._rerun[worker_id] = operation
                return None
            self._active.add(worker_id)
            return self._start(worker_id, operation)

    def _start(self, worker_id: str, operation: Callable[[], None]) -> Future[None]:
        """Start the worker's lane for one job; the lock is held."""
        job: Future[None] = Future()
        thread = Thread(
            target=self._run,
            args=(worker_id, job, operation),
            name=f"vox-cleanup-{worker_id[:8]}",
            daemon=True,
        )
        self._threads[worker_id] = thread
        thread.start()
        return job

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
                self._release_or_rerun(worker_id)
        if failure is None:
            job.set_result(None)
        else:
            job.set_exception(failure)

    def _release_or_rerun(self, worker_id: str) -> None:
        """Rerun a request declined during the job, or release the worker.

        The lock is held. The rerun keeps the worker active, so idleness
        still means every requested job has ended.
        """
        operation = self._rerun.pop(worker_id, None)
        if operation is None or self._closed:
            self._active.remove(worker_id)
            del self._threads[worker_id]
            return
        self._start(worker_id, operation)

    def shutdown(self) -> None:
        """Refuse new work, drop reruns and allow running jobs a short grace period."""
        with self._lock:
            self._closed = True
            self._rerun.clear()
            threads = list(self._threads.values())
        deadline = time.monotonic() + _DRAIN_S
        for thread in threads:
            thread.join(timeout=max(0.0, deadline - time.monotonic()))
