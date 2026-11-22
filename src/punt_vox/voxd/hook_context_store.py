"""Daemon-local rolling hook context, isolated by session."""

from __future__ import annotations

from collections import OrderedDict
from typing import Self, TypedDict, final

from punt_vox.hook_context_event import MAX_SEQUENCE, HookIdentity, HookRedaction
from punt_vox.voxd.hook_context_projection import (
    HOOK_TOOL_EVENTS,
    HookText,
    HookToolResult,
    ToolUpdate,
)
from punt_vox.voxd.hook_failures import HookFailures
from punt_vox.voxd.hook_gaps import HookGapReport, HookGaps

_MAX_SESSIONS = 64
_MAX_TOOL_EVENTS = 48
_MAX_FILES = 8
_MAX_RESULTS = 5


class HookContextSnapshot(TypedDict):
    """Bounded view of one live hook session."""

    session_started: bool
    goal: str | None  # None until a nonempty prompt arrives.
    active_files: list[str]
    last_results: list[HookToolResult]
    open_failure: HookToolResult | None  # None when no failure is unresolved.
    last_assistant_message: str | None  # None until a nonempty Stop message arrives.
    gaps: HookGapReport


@final
class HookContextStore:
    """Own the rolling, in-memory context of the most active sessions."""

    __slots__ = ("_sessions",)
    _sessions: OrderedDict[str, SessionContext]

    def __new__(cls) -> Self:
        """Initialize an empty store; never hydrate from a persisted ledger."""
        self = super().__new__(cls)
        self._sessions = OrderedDict()
        return self

    def ingest(self, payload: dict[str, object]) -> None:
        """Validate a live sender-stamped event and fold it into its session."""
        identity = HookIdentity.parse(payload)
        sequence = payload.get("relay_seq")
        if type(sequence) is not int or not 1 <= sequence <= MAX_SEQUENCE:
            msg = "hook payload requires a positive integer relay_seq"
            raise ValueError(msg)
        sanitized = HookRedaction.clean(payload)
        context = self._sessions.get(identity.session)
        if context is None:
            context = SessionContext()
            self._sessions[identity.session] = context
        context.ingest(sequence, identity.event, sanitized)
        self._sessions.move_to_end(identity.session)
        if len(self._sessions) > _MAX_SESSIONS:
            self._sessions.popitem(last=False)

    def snapshot(self, session: str) -> HookContextSnapshot:
        """Return only curated context and its gap report, never raw events."""
        context = self._sessions.get(session)
        if context is None:
            msg = "no hook context for that session"
            raise ValueError(msg)
        return context.snapshot()


@final
class SessionContext:
    """Bounded per-session view, ordered by sender sequence rather than arrival."""

    __slots__ = ("_assistant", "_failures", "_gaps", "_goal", "_started", "_tools")
    _assistant: tuple[int, str]
    _failures: HookFailures
    _gaps: HookGaps
    _goal: tuple[int, str]
    _started: bool
    _tools: dict[int, ToolUpdate]

    def __new__(cls) -> Self:
        """Create a new view for a session with at least one live event."""
        self = super().__new__(cls)
        self._assistant = (0, "")
        self._failures = HookFailures()
        self._gaps = HookGaps()
        self._goal = (0, "")
        self._started = False
        self._tools = {}
        return self

    def ingest(self, sequence: int, event: str, payload: dict[str, object]) -> None:
        """Fold a fresh event; duplicates must not change the projection."""
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
                update = ToolUpdate.from_payload(payload, event)
                self._failures.observe(sequence, update)
                self._tools[sequence] = update
                if len(self._tools) > _MAX_TOOL_EVENTS:
                    del self._tools[min(self._tools)]
            case _:
                pass

    @staticmethod
    def _newest(previous: tuple[int, str], sequence: int, text: str) -> tuple[int, str]:
        return (sequence, text) if sequence > previous[0] and text else previous

    def snapshot(self) -> HookContextSnapshot:
        """Project the newest goals, files, results, and still-open failure."""
        files: dict[str, None] = {}
        results: list[HookToolResult] = []
        for sequence, update in sorted(self._tools.items()):
            if update.file:
                files.pop(update.file, None)
                files[update.file] = None
            if not update.completed:
                continue
            if update.result:
                results.append(update.as_result(sequence))
        return {
            "session_started": self._started,
            "goal": self._goal[1] or None,
            "active_files": list(files)[-_MAX_FILES:],
            "last_results": results[-_MAX_RESULTS:],
            "open_failure": self._failures.latest(),
            "last_assistant_message": self._assistant[1] or None,
            "gaps": self._gaps.report(),
        }
