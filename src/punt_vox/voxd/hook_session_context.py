"""One session projection, independent of store eviction and admission."""

from __future__ import annotations

from typing import Self, TypedDict, final

from punt_vox.voxd.hook_context_projection import (
    HOOK_TOOL_EVENTS,
    HookText,
    HookToolResult,
)
from punt_vox.voxd.hook_decisions import HookDecisions, PendingDecision
from punt_vox.voxd.hook_gaps import HookGapReport, HookGaps
from punt_vox.voxd.hook_input_history import HookInputHistory
from punt_vox.voxd.hook_tool_history import HookToolHistory


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


@final
class SessionContext:
    """Bounded per-session view, ordered by sender sequence rather than arrival."""

    __slots__ = (
        "_assistant",
        "_decisions",
        "_gaps",
        "_goal",
        "_input",
        "_started",
        "_tools",
    )
    _decisions: HookDecisions
    _input: HookInputHistory
    _assistant: tuple[int, str]
    _gaps: HookGaps
    _goal: tuple[int, str]
    _started: bool
    _tools: HookToolHistory

    def __new__(cls) -> Self:
        """Create a new view for a session with at least one live event."""
        self = super().__new__(cls)
        self._assistant = (0, "")
        self._decisions = HookDecisions()
        self._gaps = HookGaps()
        self._goal = (0, "")
        self._input = HookInputHistory()
        self._started = False
        self._tools = HookToolHistory()
        return self

    def ingest(
        self,
        sequence: int,
        event: str,
        payload: dict[str, object],
        *,
        worker_input: bool = False,
    ) -> None:
        """Fold a fresh event; duplicates must not change the projection."""
        if worker_input:
            self._worker_event(sequence, event, payload)
        if not self._gaps.observe(sequence):
            return
        match event:
            case "SessionStart":
                self._started = True
            case "UserPromptSubmit":
                self._goal = self._newest(
                    self._goal, sequence, HookText.clip(payload.get("prompt"))
                )
            case "Stop":
                text = HookText.clip(payload.get("last_assistant_message"))
                self._assistant = self._newest(self._assistant, sequence, text)
            case _ if event in HOOK_TOOL_EVENTS:
                self._tools.ingest(sequence, event, payload)
            case _:
                pass

    def _worker_event(
        self, sequence: int, event: str, payload: dict[str, object]
    ) -> None:
        if event == "UserPromptSubmit":
            self._input.ingest(sequence, payload.get("prompt"))
        self._decisions.ingest(sequence, event, payload)

    @staticmethod
    def _newest(previous: tuple[int, str], sequence: int, text: str) -> tuple[int, str]:
        return (sequence, text) if sequence > previous[0] and text else previous

    @property
    def decisions(self) -> HookDecisions:
        """Expose decision operations without exposing mutable snapshot fields."""
        return self._decisions

    def last_input(self) -> tuple[int, str]:
        """Input acknowledgement is independent of Stop and turn completion."""
        return self._input.last()

    def inputs_since(self, baseline: int) -> list[tuple[int, str]]:
        """Retain intermediate receipts even when a newer input arrives."""
        return self._input.since(baseline)

    def snapshot(self) -> HookContextSnapshot:
        """Project the newest goals, files, results, and still-open failure."""
        files, results = self._tools.files_and_results()
        return {
            "session_started": self._started,
            "goal": self._goal[1] or None,
            "active_files": files,
            "last_results": results,
            "open_failure": self._tools.open_failure(),
            "last_assistant_message": self._assistant[1] or None,
            "gaps": self._gaps.report(),
            "pending_decision": self._decisions.pending(),
        }
