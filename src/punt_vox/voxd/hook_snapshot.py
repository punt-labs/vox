"""Curated hook projection and durable restart ordering."""

from __future__ import annotations

from typing import TypedDict

from punt_vox.voxd.hook_context_projection import HookToolResult
from punt_vox.voxd.hook_decision_types import PendingDecision
from punt_vox.voxd.hook_gaps import HookGapReport
from punt_vox.voxd.hook_tool_history import ToolHistoryCheckpoint


class HookContextSnapshot(TypedDict):
    """Bounded view of one live hook session."""

    session_started: bool
    goal: str | None  # None until a nonempty prompt arrives.
    active_files: list[str]
    last_results: list[HookToolResult]
    open_failure: HookToolResult | None  # None when no failure is unresolved.
    last_assistant_message: str | None  # None until a nonempty Stop message arrives.
    gaps: HookGapReport
    pending_decision: PendingDecision | None  # None when no question is outstanding.
    prompt_epoch: int


class WorkerHookCheckpoint(HookContextSnapshot):
    """Add ordering metadata to the durable managed-worker projection."""

    result_seq: int
    worker_seq: int
    goal_seq: int
    tool_history_checkpoint: ToolHistoryCheckpoint
