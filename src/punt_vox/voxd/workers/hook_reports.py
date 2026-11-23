"""Deduplicate checkpointed worker milestones, including delayed hook delivery."""

from __future__ import annotations

from collections import OrderedDict
from typing import Self, cast, final

from punt_vox.voxd.hook_context_projection import HookText
from punt_vox.voxd.hook_gaps import HookGaps
from punt_vox.voxd.voice_call.progress import WorkerProgress


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
        self, session: str, payload: dict[str, object]
    ) -> WorkerProgress | None:
        """Project a validated event only after its checkpoint succeeded."""
        history = self._sessions.setdefault(session, HookGaps())
        self._sessions.move_to_end(session)
        if len(self._sessions) > 64:
            self._sessions.popitem(last=False)
        seq = cast("int", payload["relay_seq"])
        if not history.observe(seq):
            return None
        return self._event(session, seq, payload)

    @staticmethod
    def _event(
        session: str, seq: int, payload: dict[str, object]
    ) -> WorkerProgress | None:
        if payload.get("hook_event_name") == "Stop":
            return WorkerProgress(
                session,
                seq,
                "finished",
                HookText.clip(payload.get("last_assistant_message")),
            )
        notification = payload.get("notification_type")
        if payload.get("hook_event_name") == "Notification" and notification in {
            "permission_prompt",
            "idle_prompt",
        }:
            return WorkerProgress(
                session,
                seq,
                "needs_decision",
                HookText.clip(payload.get("message")),
                str(notification),
            )
        return None
