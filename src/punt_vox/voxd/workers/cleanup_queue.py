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
        A lane that cannot start returns a job already failed with the
        reason, never a job that waits forever.
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
        """Start the worker's lane for one job; the lock is held.

        A lane that cannot start (the process is out of threads) releases
        the worker, fails the job so a waiting reply hears it, and logs
        under the worker id; the disk marker keeps the debt owed.
        """
        job: Future[None] = Future()
        thread = Thread(
            target=self._run,
            args=(worker_id, job, operation),
            name=f"vox-cleanup-{worker_id[:8]}",
            daemon=True,
        )
        try:
            thread.start()
        except RuntimeError as exc:
            logger.exception("Worker %s cleanup lane could not start", worker_id)
            self._release(worker_id)
            job.set_exception(exc)
            return job
        self._threads[worker_id] = thread
        return job

    def _run(
        self, worker_id: str, job: Future[None], operation: Callable[[], None]
    ) -> None:
        """Run one job, release or detach its rerun, settle, then rerun.

        The worker is released before the future resolves, so a caller whose
        wait ends and who submits again at once gets a fresh lane, not a
        parked rerun. A rerun starts only after the future is settled, so a
        waiting reply never depends on a second lane coming up.
        """
        failure = self._attempt(worker_id, operation)
        rerun = self._detach(worker_id)
        self._settle(job, failure)
        if rerun is not None:
            with self._lock:
                self._start(worker_id, rerun)

    @staticmethod
    def _attempt(
        worker_id: str, operation: Callable[[], None]
    ) -> Exception | None:  # PY-TS-14: None is the job's success.
        """Run the operation; log and return its failure instead of raising."""
        try:
            operation()
        except Exception as exc:
            logger.exception("Worker %s cleanup job failed", worker_id)
            return exc
        return None

    def _detach(
        self, worker_id: str
    ) -> Callable[[], None] | None:  # PY-TS-14: None releases the worker.
        """Take the worker's parked rerun, or release the worker; under the lock."""
        with self._lock:
            rerun = self._take_rerun(worker_id)
            if rerun is None:
                self._release(worker_id)
            return rerun

    def _take_rerun(
        self, worker_id: str
    ) -> Callable[[], None] | None:  # PY-TS-14: None when nothing is parked.
        """Pop the worker's parked rerun; a closed queue drops it with a log.

        The disk marker keeps the debt owed, but nothing runs it until the
        next start, so the drop names the worker. The lock is held.
        """
        rerun = self._rerun.pop(worker_id, None)
        if rerun is not None and self._closed:
            self._dropped(worker_id)
            return None
        return rerun

    @staticmethod
    def _dropped(worker_id: str) -> None:
        """Say which worker's requested cleanup did not run; the queue closed."""
        logger.warning(
            "Worker %s cleanup request dropped: the queue is closed", worker_id
        )

    @staticmethod
    def _settle(
        job: Future[None],
        failure: Exception | None,  # PY-TS-14: None is the job's success.
    ) -> None:
        """Resolve the job's future with its outcome."""
        if failure is None:
            job.set_result(None)
        else:
            job.set_exception(failure)

    def _release(self, worker_id: str) -> None:
        """Forget the worker's lane; the lock is held."""
        self._active.discard(worker_id)
        self._threads.pop(worker_id, None)

    def shutdown(self) -> None:
        """Refuse new work, drop reruns and allow running jobs a short grace period."""
        with self._lock:
            self._closed = True
            for worker_id in self._rerun:
                self._dropped(worker_id)
            self._rerun.clear()
            threads = list(self._threads.values())
        deadline = time.monotonic() + _DRAIN_S
        for thread in threads:
            thread.join(timeout=max(0.0, deadline - time.monotonic()))
