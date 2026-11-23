"""Retirement queue shutdown behavior."""

from threading import Event

from punt_vox.voxd.workers.cleanup_queue import WorkerCleanupQueue


def test_shutdown_cancels_waiting_and_future_work() -> None:
    queue = WorkerCleanupQueue()
    started, release, ran = Event(), Event(), Event()

    def hold() -> None:
        started.set()
        assert release.wait(3)

    running = queue.submit(hold)
    try:
        assert started.wait(1)
        waiting = queue.submit(ran.set)
        queue.shutdown()
        assert waiting.cancelled()
        assert queue.submit(ran.set).cancelled()
        assert not running.done()
    finally:
        release.set()
    running.result(timeout=1)
    assert not ran.is_set()
