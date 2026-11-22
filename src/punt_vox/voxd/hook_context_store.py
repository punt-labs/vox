"""Daemon-local rolling hook context, isolated by session."""

from __future__ import annotations

from collections import OrderedDict
from typing import Self, TypedDict, final

from punt_vox.hook_context_event import MAX_SEQUENCE, HookIdentity, HookRedaction
from punt_vox.voxd.hook_context_projection import (
    HOOK_TOOL_EVENTS,
    HookText,
    HookToolResult,
)
from punt_vox.voxd.hook_gaps import HookGapReport, HookGaps
from punt_vox.voxd.hook_tool_history import HookToolHistory

_MAX_SESSIONS = 64


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

    def last_input(self, session: str) -> tuple[int, str]:
        """Return the newest submitted prompt without changing the snapshot schema."""
        context = self._sessions.get(session)
        return context.last_input() if context is not None else (0, "")


@final
class SessionContext:
    """Bounded per-session view, ordered by sender sequence rather than arrival."""

    __slots__ = ("_assistant", "_gaps", "_goal", "_input", "_started", "_tools")
    _input: tuple[int, str]
    _assistant: tuple[int, str]
    _gaps: HookGaps
    _goal: tuple[int, str]
    _started: bool
    _tools: HookToolHistory

    def __new__(cls) -> Self:
        """Create a new view for a session with at least one live event."""
        self = super().__new__(cls)
        self._assistant = (0, "")
        self._gaps = HookGaps()
        self._goal = (0, "")
        self._input = (0, "")
        self._started = False
        self._tools = HookToolHistory()
        return self

    def ingest(self, sequence: int, event: str, payload: dict[str, object]) -> None:
        """Fold a fresh event; duplicates must not change the projection."""
        if not self._gaps.observe(sequence):
            return
        match event:
            case "SessionStart":
                self._started = True
            case "UserPromptSubmit":
                if sequence > self._input[0]:
                    self._input = (sequence, HookText.clip(payload.get("prompt")))
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

    @staticmethod
    def _newest(previous: tuple[int, str], sequence: int, text: str) -> tuple[int, str]:
        return (sequence, text) if sequence > previous[0] and text else previous

    def last_input(self) -> tuple[int, str]:
        """Input acknowledgement is independent of Stop and turn completion."""
        return self._input

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
        }
