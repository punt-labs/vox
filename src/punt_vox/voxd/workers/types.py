"""Lightweight worker serialization contracts, independent of the codec."""

from __future__ import annotations

from typing import Final, TypedDict, final

from punt_vox.voxd.workers.model import WorkerState

__all__ = ["WorkerDeadlines", "WorkerPayload"]


@final
class WorkerDeadlines:
    """Seconds the daemon and its clients agree a worker operation may take.

    The client's start budget must outlive the daemon's worktree deadline,
    or a start that runs to that deadline times out on the client while the
    daemon is still rolling back and never hears the spoken reason.
    """

    __slots__ = ()
    # `git worktree add` on a loaded machine has taken over 20 s.
    CREATE: Final = 90
    # Resolution, lock wait, the saved record and a provider stop on rollback.
    START_MARGIN: Final = 30
    START: Final = CREATE + START_MARGIN
    CONTROL: Final = 30

    @classmethod
    def budget(cls, operation: str) -> int:
        """Return the client budget for one worker RPC."""
        return cls.START if operation == "start" else cls.CONTROL


class WorkerPayload(TypedDict):
    """Disk/wire memento, never a live mutable worker entity."""

    id: str
    provider: str
    harness: str
    session: str
    project: str
    worktree: str
    branch: str
    task: str
    state: WorkerState
    created_at: str
    last_report: dict[str, object]  # PY-TS-14: extensible harness JSON at the edge.
