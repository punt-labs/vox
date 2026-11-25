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
    # The bounded phases of one start, in order. The client budget is their
    # sum plus a reply margin, so a start in which every phase runs to its
    # deadline still answers before the client gives up.
    #
    # `git rev-parse --show-toplevel` resolving the project.
    RESOLVE: Final = 5
    # Waiting for another worker call to release the actor; past this the
    # start is refused with the reason, since the lock is held for a whole
    # call.
    LOCK_WAIT: Final = 15
    # `git worktree add`; on a loaded machine it has taken over 20 s.
    CREATE: Final = 90
    # The provider bringing its session up and the harness settling.
    PROVIDER_START: Final = 15
    # Handing the harness its environment over the local socket.
    ENV_DELIVERY: Final = 15
    # The saved record, a provider stop on rollback, and the reply itself.
    REPLY_MARGIN: Final = 10
    START: Final = (
        RESOLVE + LOCK_WAIT + CREATE + PROVIDER_START + ENV_DELIVERY + REPLY_MARGIN
    )
    CONTROL: Final = 30

    @classmethod
    def start_phases(cls) -> tuple[int, ...]:
        """Return every bounded phase of a start, in order, without the margin."""
        return (
            cls.RESOLVE,
            cls.LOCK_WAIT,
            cls.CREATE,
            cls.PROVIDER_START,
            cls.ENV_DELIVERY,
        )

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
