"""Daemon-local rolling hook context, isolated by session."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, final

from punt_vox.hook_context_event import MAX_SEQUENCE, HookIdentity, HookRedaction
from punt_vox.voxd.hook_session_context import SessionContext
from punt_vox.voxd.hook_sessions import HookSessions

if TYPE_CHECKING:
    from punt_vox.voxd.hook_snapshot import HookContextSnapshot, WorkerHookCheckpoint


@final
class HookContextStore:
    """Own the rolling, in-memory context of the most active sessions."""

    __slots__ = ("_sessions",)
    _sessions: HookSessions

    def __new__(cls) -> Self:
        """Initialize an empty store before restoring managed checkpoints."""
        self = super().__new__(cls)
        self._sessions = HookSessions()
        return self

    def ingest(self, payload: dict[str, object], *, worker_input: bool = False) -> None:
        """Fold context, reserving receipts for server-admitted worker hooks."""
        identity = HookIdentity.parse(payload)
        sequence = payload.get("relay_seq")
        if type(sequence) is not int or not 1 <= sequence <= MAX_SEQUENCE:
            msg = "hook payload requires a positive integer relay_seq"
            raise ValueError(msg)
        sanitized = HookRedaction.clean(payload)
        context = self._sessions.get(identity.session)
        if context is None:
            context = SessionContext()
        context.ingest(sequence, identity.event, sanitized, worker_input=worker_input)
        self._sessions.remember(identity.session, context, managed=worker_input)

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

    def inputs_since(self, session: str, baseline: int) -> list[tuple[int, str]]:
        """Return bounded recent inputs without changing the snapshot schema."""
        context = self._sessions.get(session)
        return context.inputs_since(baseline) if context is not None else []

    def decision_baseline(self, session: str) -> int:
        """Return the cursor of an outstanding worker question."""
        context = self._sessions.get(session)
        return context.decisions.baseline() if context is not None else 0

    def worker_sequence(self, session: str) -> int:
        """Return the newest admitted worker hook, excluding shared context hooks."""
        context = self._sessions.get(session)
        return context.decisions.latest if context is not None else 0

    def checkpoint(self, session: str) -> WorkerHookCheckpoint | None:
        """Read the managed projection with its sequence ordering."""
        context = self._sessions.get(session)
        return context.checkpoint() if context is not None else None

    def restore(self, session: str, checkpoint: WorkerHookCheckpoint) -> None:
        """Rebuild one managed session without replaying hooks or progress events."""
        context = SessionContext()
        context.restore(checkpoint)
        self._sessions.remember(session, context, managed=True)

    def release_worker(self, session: str) -> None:
        """Return a terminal worker's context to the ordinary retention limit."""
        self._sessions.release(session)

    def decision_receipts(self, session: str, baseline: int) -> list[int]:
        """Return progress acknowledging a question present at the baseline."""
        context = self._sessions.get(session)
        return context.decisions.receipts(baseline) if context is not None else []
