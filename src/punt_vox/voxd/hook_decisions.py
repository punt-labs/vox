"""Ordered decision state and bounded acknowledgements of dialog answers."""

from __future__ import annotations

from collections import deque
from typing import Self, final

from punt_vox.voxd.hook_context_projection import HookText
from punt_vox.voxd.hook_decision_types import PendingDecision


@final
class HookDecisions:
    """Ignore stale events when tracking a worker's outstanding question."""

    __slots__ = ("_epoch", "_high", "_pending", "_receipts")
    _epoch: int
    _high: int
    _pending: PendingDecision | None  # None when no decision is outstanding.
    _receipts: deque[tuple[int, int]]

    def __new__(cls) -> Self:
        """Create an empty question and acknowledgement window."""
        self = super().__new__(cls)
        self._epoch = 0
        self._high = 0
        self._pending = None
        self._receipts = deque(maxlen=64)
        return self

    def ingest(self, seq: int, event: str, payload: dict[str, object]) -> None:
        """Acknowledge a pending dialog on its next newer worker hook."""
        if seq <= self._high:
            return
        self._high = seq
        if (
            self._pending is not None
            and self._pending["notification_type"] == "permission_prompt"
        ):
            self._receipts.append((self._pending["seq"], seq))
        if event != "Notification":
            self._pending = None
            if event == "Stop":
                self._epoch = seq
        elif payload.get("notification_type") in {"permission_prompt", "idle_prompt"}:
            self._epoch = seq
            self._pending = {
                "notification_type": str(payload["notification_type"]),
                "message": HookText.clip(payload.get("message")),
                "seq": seq,
            }

    def pending(self) -> PendingDecision | None:
        """Return a detached view, or None when the worker can proceed."""
        return self._pending.copy() if self._pending is not None else None

    @property
    def latest(self) -> int:
        """Return the newest admitted worker hook, even without a pending question."""
        return self._high

    @property
    def prompt_epoch(self) -> int:
        """Keep one identity until the next admitted result or decision prompt."""
        return self._epoch

    def baseline(self) -> int:
        """Return the newest event only while waiting for a permission choice."""
        return (
            self._high
            if self._pending is not None
            and self._pending["notification_type"] == "permission_prompt"
            else 0
        )

    def receipts(self, baseline: int) -> list[int]:
        """Only accept progress from a question already present at delivery."""
        return [seq for question, seq in self._receipts if question <= baseline < seq]
