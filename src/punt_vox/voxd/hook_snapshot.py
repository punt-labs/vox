"""Curated hook projection and durable restart ordering."""

from __future__ import annotations

from typing import TypedDict, final

from punt_vox.voxd.hook_context_projection import HookToolResult
from punt_vox.voxd.hook_decision_types import PendingDecision
from punt_vox.voxd.hook_gaps import HookGapReport
from punt_vox.voxd.hook_tool_history import ToolHistoryCheckpoint


class HookContextSnapshot(TypedDict):
    """Bounded view of one live hook session."""

    session_started: bool
    goal: str | None  # None until a nonempty prompt arrives.
    active_files: list[str]
    in_flight_tool: str | None  # None when the worker is between tools.
    last_results: list[HookToolResult]
    open_failure: HookToolResult | None  # None when no failure is unresolved.
    last_assistant_message: str | None  # None until a nonempty Stop message arrives.
    gaps: HookGapReport
    pending_decision: PendingDecision | None  # None when no question is outstanding.
    prompt_epoch: int
    turn_seq: int  # Newest prompt or tool event; a Stop after it ends the turn.
    last_event_at: float  # Epoch seconds of the newest worker hook; 0.0 if none.


class WorkerHookCheckpoint(HookContextSnapshot):
    """Add ordering metadata to the durable managed-worker projection."""

    result_seq: int
    worker_seq: int
    goal_seq: int
    prompt_seq: int  # Newest prompt of any text; an empty one still ends a tool.
    tool_history_checkpoint: ToolHistoryCheckpoint


@final
class CheckpointOrdering:
    """Check durable ordering fields before a checkpoint becomes live."""

    __slots__ = ()

    @staticmethod
    def validate(checkpoint: WorkerHookCheckpoint) -> None:
        """Refuse invalid sequence values without mutating the projection."""
        sequences = (
            checkpoint["worker_seq"],
            checkpoint["prompt_epoch"],
            checkpoint["result_seq"],
            checkpoint["goal_seq"],
            checkpoint["prompt_seq"],
        )
        if not all(
            type(seq) is int and 0 <= seq <= checkpoint["gaps"]["highest_seen"]
            for seq in sequences
        ):
            raise ValueError("Invalid worker hook checkpoint ordering")
