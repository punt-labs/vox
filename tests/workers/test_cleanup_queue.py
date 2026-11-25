"""Cleanup lanes: one job per worker, workers independent, bounded shutdown."""

import logging
from threading import Event

import pytest

from punt_vox.voxd.workers.cleanup_queue import WorkerCleanupQueue


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
