"""Provider-neutral worker milestones delivered to live call subscribers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class WorkerProgress:
    """One already-admitted, deduplicated worker milestone."""

    worker_id: str
    seq: int
    kind: Literal["finished", "needs_decision"]
    text: str
    notification_type: str = ""
