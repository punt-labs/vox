"""Managed hook session retention."""

from punt_vox.voxd.hook_session_context import SessionContext
from punt_vox.voxd.hook_sessions import HookSessions


def test_worker_context_only_ages_out_after_release() -> None:
    sessions = HookSessions()
    worker = SessionContext()
    sessions.remember("worker", worker, managed=True)
    for index in range(sessions.LIMIT + 1):
        sessions.remember(str(index), SessionContext(), managed=False)
    assert sessions.get("0") is None
    assert sessions.get("worker") is worker

    sessions.release("worker")
    for index in range(sessions.LIMIT):
        sessions.remember(f"next-{index}", SessionContext(), managed=False)
    assert sessions.get("worker") is None
