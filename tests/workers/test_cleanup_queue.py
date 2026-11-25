"""Cleanup lanes: one job per worker, workers independent, bounded shutdown."""

from __future__ import annotations

import logging
import time
from threading import Event, Thread
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from punt_vox.voxd.workers.cleanup_queue import WorkerCleanupQueue

if TYPE_CHECKING:
    from collections.abc import Callable


def test_shutdown_refuses_new_work_and_drops_reruns() -> None:
    queue = WorkerCleanupQueue()
    started, release, ran = Event(), Event(), Event()

    def hold() -> None:
        started.set()
        assert release.wait(3)

    running = queue.submit("running", hold)
    assert running is not None
    try:
        assert started.wait(1)
        assert not queue.is_idle()
        assert queue.submit("running", ran.set) is None
        queue.shutdown()
        assert queue.submit("closed", ran.set) is None
        assert not running.done()
    finally:
        release.set()
    running.result(timeout=1)
    assert queue.is_idle()
    assert not ran.is_set()


def test_jobs_for_different_workers_run_at_once() -> None:
    """One worker's slow job never holds another worker's quick one."""
    queue = WorkerCleanupQueue()
    started, release = Event(), Event()

    def hold() -> None:
        started.set()
        assert release.wait(3)

    blocked = queue.submit("slow", hold)
    assert blocked is not None
    try:
        assert started.wait(1)
        quick = queue.submit("quick", lambda: None)
        assert quick is not None
        quick.result(timeout=1)
    finally:
        release.set()
    blocked.result(timeout=1)
    assert queue.is_idle()


def test_failed_job_log_names_the_worker(caplog: pytest.LogCaptureFixture) -> None:
    """An exception escaping a job is logged under the worker it belonged to."""
    queue = WorkerCleanupQueue()
    with caplog.at_level(logging.ERROR, logger="punt_vox.voxd.workers.cleanup_queue"):
        job = queue.submit("w-42", lambda: _raise(RuntimeError("save exploded")))
        assert job is not None
        with pytest.raises(RuntimeError, match="save exploded"):
            job.result(timeout=1)
    [record] = caplog.records
    assert "w-42" in record.getMessage()
    assert record.exc_info is not None


def _raise(exc: Exception) -> None:
    raise exc


def _lane_start_failing_after(
    first: Event,
) -> Callable[[Thread], None]:
    """Thread.start that works until *first* is set, then refuses every lane."""
    original = Thread.start

    def start(thread: Thread) -> None:
        if thread.name.startswith("vox-cleanup") and first.is_set():
            raise RuntimeError("can't start new thread")
        original(thread)

    return start


def test_a_rerun_lane_that_cannot_start_still_settles_the_awaited_job(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A reply waiting on job A hears A end even when A's rerun lane fails.

    The rerun starts after A's future is settled; a start failure releases
    the worker, so the queue goes idle, and is logged under the worker id.
    """
    queue = WorkerCleanupQueue()
    started, release, refuse = Event(), Event(), Event()

    def hold() -> None:
        started.set()
        assert release.wait(3)

    with (
        patch.object(Thread, "start", _lane_start_failing_after(refuse)),
        caplog.at_level(logging.ERROR, logger="punt_vox.voxd.workers.cleanup_queue"),
    ):
        first = queue.submit("w-1", hold)
        assert first is not None
        assert started.wait(1)
        assert queue.submit("w-1", lambda: None) is None
        refuse.set()
        release.set()
        first.result(timeout=1)
        deadline = time.monotonic() + 1
        while not queue.is_idle():
            assert time.monotonic() < deadline
            time.sleep(0.01)
    [record] = caplog.records
    assert "w-1" in record.getMessage()
    assert "could not start" in record.getMessage()
    assert record.exc_info is not None


def test_a_lane_that_cannot_start_on_submit_fails_the_job_and_releases(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A submit whose thread cannot start returns a failed job, not a hang."""
    queue = WorkerCleanupQueue()
    refuse = Event()
    refuse.set()
    with (
        patch.object(Thread, "start", _lane_start_failing_after(refuse)),
        caplog.at_level(logging.ERROR, logger="punt_vox.voxd.workers.cleanup_queue"),
    ):
        job = queue.submit("w-2", lambda: None)
    assert job is not None
    with pytest.raises(RuntimeError, match="can't start new thread"):
        job.result(timeout=1)
    assert queue.is_idle()
    [record] = caplog.records
    assert "w-2" in record.getMessage()


def test_a_job_releases_its_worker_before_its_future_resolves() -> None:
    """A caller whose wait ends may submit again at once and get a fresh lane.

    If the future resolved first, that submit would find the worker still
    active and park as a rerun, returning no job, and a stop that waited on
    the first job would return with the second's cleanup still owed.
    """
    queue = WorkerCleanupQueue()
    releasing, go = Event(), Event()
    original = WorkerCleanupQueue._release

    def slow_release(self: WorkerCleanupQueue, worker_id: str) -> None:
        releasing.set()
        assert go.wait(2)
        original(self, worker_id)

    with patch.object(WorkerCleanupQueue, "_release", slow_release):
        job = queue.submit("w-3", lambda: None)
        assert job is not None
        assert releasing.wait(1)
        settled_before_release = job.done()
        go.set()
        job.result(timeout=1)
    assert queue.is_idle()
    assert not settled_before_release


def test_a_rerun_dropped_at_shutdown_is_logged_with_the_worker(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A parked rerun the shutdown drops leaves a line naming the worker."""
    queue = WorkerCleanupQueue()
    started, release = Event(), Event()

    def hold() -> None:
        started.set()
        assert release.wait(3)

    job = queue.submit("w-4", hold)
    assert job is not None
    assert started.wait(1)
    assert queue.submit("w-4", lambda: None) is None
    with caplog.at_level(logging.WARNING, logger="punt_vox.voxd.workers.cleanup_queue"):
        queue.shutdown()
    release.set()
    job.result(timeout=1)
    [record] = [r for r in caplog.records if "dropped" in r.getMessage()]
    assert "w-4" in record.getMessage()
    assert "closed" in record.getMessage()


def test_a_rerun_detached_before_shutdown_never_starts_after_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A shutdown that lands between detach and start wins; no lane escapes.

    shutdown takes its thread snapshot under the lock; a rerun that started
    after that would outlive the bounded drain. The rerun path rechecks the
    closed flag under the lock, drops with the log line, and the queue goes
    idle.
    """
    queue = WorkerCleanupQueue()
    detached, proceed = Event(), Event()
    original_detach = WorkerCleanupQueue._detach

    def detach_then_pause(self: WorkerCleanupQueue, worker_id: str) -> object:
        rerun = original_detach(self, worker_id)
        if rerun is not None:
            detached.set()
            assert proceed.wait(3)
        return rerun

    started, release = Event(), Event()

    def hold() -> None:
        started.set()
        assert release.wait(3)

    with (
        patch.object(WorkerCleanupQueue, "_detach", detach_then_pause),
        patch.object(WorkerCleanupQueue, "_start", wraps=queue._start) as start,
        caplog.at_level(logging.WARNING, logger="punt_vox.voxd.workers.cleanup_queue"),
    ):
        job = queue.submit("w-6", hold)
        assert job is not None
        assert started.wait(1)
        assert queue.submit("w-6", lambda: None) is None
        release.set()
        assert detached.wait(1)
        queue.shutdown()
        proceed.set()
        job.result(timeout=1)
        deadline = time.monotonic() + 1
        while not queue.is_idle():
            assert time.monotonic() < deadline
            time.sleep(0.01)
    assert start.call_count == 1  # the first lane only; the rerun never started
    [record] = [r for r in caplog.records if "dropped" in r.getMessage()]
    assert "w-6" in record.getMessage()
