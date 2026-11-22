"""Typed worker identity, lifecycle state and durable report."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, final

__all__ = ["WorkerRecord", "WorkerState"]

type WorkerState = Literal["starting", "ready", "exited", "stopped"]


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class WorkerRecord:
    """An immutable lifecycle checkpoint; transitions replace the whole value."""

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
    # PY-TS-14: a read-only snapshot with an open, harness-specific JSON schema.
    last_report: Mapping[str, object]

    def __post_init__(self) -> None:
        """Detach the snapshot so callers cannot mutate the stored checkpoint."""
        object.__setattr__(
            self, "last_report", MappingProxyType(deepcopy(dict(self.last_report)))
        )
