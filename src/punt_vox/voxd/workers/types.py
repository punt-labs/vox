"""Lightweight worker serialization contracts, independent of the codec."""

from __future__ import annotations

from typing import TypedDict

from punt_vox.voxd.workers.model import WorkerState

__all__ = ["WorkerPayload"]


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
