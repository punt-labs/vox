"""Deduplicate checkpointed worker milestones, including delayed hook delivery."""

from __future__ import annotations

from collections import OrderedDict
from typing import TYPE_CHECKING, Self, cast, final

from punt_vox.voxd.hook_context_projection import HookText
from punt_vox.voxd.hook_gaps import HookGaps
from punt_vox.voxd.voice_call.progress_types import WorkerProgress

if TYPE_CHECKING:
    from punt_vox.voxd.hook_decision_types import PendingDecision


@final
class HookReports:
    """Track only successfully checkpointed sequences in a bounded session window."""

    __slots__ = ("_sessions",)
    _sessions: OrderedDict[str, HookGaps]

    def __new__(cls) -> Self:
        """Create an empty delivery window matching the context store's capacity."""
        self = super().__new__(cls)
        self._sessions = OrderedDict()
        return self

    def observe(
        self,
        session: str,
        payload: dict[str, object],
        pending: PendingDecision | None,
        *,
        latest: int,
    ) -> WorkerProgress | None:
        """Project a validated event only after its checkpoint succeeded."""
        history = self._sessions.setdefault(session, HookGaps())
        self._sessions.move_to_end(session)
        if len(self._sessions) > 64:
            self._sessions.popitem(last=False)
        seq = cast("int", payload["relay_seq"])
        if not history.observe(seq):
            return None
        if payload.get("hook_event_name") == "Stop" and seq < latest:
            return None
        return self._event(session, seq, payload, pending)

    @staticmethod
    def _event(
        session: str,
        seq: int,
        payload: dict[str, object],
        pending: PendingDecision | None,
    ) -> WorkerProgress | None:
        if payload.get("hook_event_name") == "Stop":
            return WorkerProgress(
                session,
                seq,
                "finished",
                HookText.clip(payload.get("last_assistant_message")),
            )
        if (
            payload.get("hook_event_name") == "Notification"
            and pending is not None
            and pending["seq"] == seq
        ):
            return WorkerProgress(
                session,
                seq,
                "needs_decision",
                pending["message"],
                pending["notification_type"],
            )
        return None
