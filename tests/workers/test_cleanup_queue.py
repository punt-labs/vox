"""Retirement queue shutdown behavior."""

from threading import Event

from punt_vox.voxd.workers.cleanup_queue import WorkerCleanupQueue


def test_shutdown_cancels_waiting_and_future_work() -> None:
    queue = WorkerCleanupQueue()
    started, release, ran = Event(), Event(), Event()

    def hold() -> None:
        started.set()
        assert release.wait(3)

    running = queue.submit("running", hold)
    assert running is not None
    try:
        assert started.wait(1)
        assert queue.submit("running", ran.set) is None
        waiting = queue.submit("waiting", ran.set)
        assert waiting is not None
        queue.shutdown()
        assert waiting.cancelled()
        assert queue.submit("closed", ran.set) is None
        assert not running.done()
    finally:
        release.set()
    running.result(timeout=1)
    assert not queue._active
    assert not ran.is_set()
