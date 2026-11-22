"""Typed worker identity, lifecycle state and durable report."""

from __future__ import annotations

from typing import Literal, TypedDict

type WorkerState = Literal["starting", "ready", "exited", "stopped"]


class WorkerRecord(TypedDict):
    """Carry worker data unchanged between registry and serialization edges."""

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
    last_report: dict[str, object]
