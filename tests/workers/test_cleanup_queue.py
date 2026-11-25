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
