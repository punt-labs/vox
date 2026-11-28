"""Trace what a tool told the agent, keeping prompts and worker text out."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Self, cast, final

from punt_vox.voxd.voice_call.call_log import CallLog

if TYPE_CHECKING:
    from punt_vox.voxd.voice_agent.events import ClientToolCall
    from punt_vox.voxd.voice_agent.trace import EventTrace

__all__ = ["ToolTrace"]


@final
class ToolTrace:
    """Write the trace note a successful tool result earns, if it earns one."""

    __slots__ = ("_trace",)
    _trace: EventTrace

    def __new__(cls, trace: EventTrace) -> Self:
        """Bind the call's trace."""
        self = super().__new__(cls)
        self._trace = trace
        return self

    def record(self, call: ClientToolCall, result: str) -> None:
        """Trace a steer receipt, a worker start or a status reading.

        A note that cannot be written is logged, never raised: the trace is a
        record of the call, and losing it must not cost the call an answer.
        """
        try:
            if call.tool_name == "steer_worker":
                self._receipt(call, result)
            elif call.tool_name == "start_worker":
                self._started(result)
            elif call.tool_name == "worker_status":
                self._status(result)
        except (OSError, ValueError, AttributeError) as exc:
            CallLog.untraced(call.tool_name, exc)

    def _started(self, result: str) -> None:
        """Trace which worker a start created; its task is the person's words.

        A start held for a spoken yes answers with a status and created nothing.
        """
        started = self._object(result)
        worker_id = started.get("worker_id")
        if "status" in started or not isinstance(worker_id, str):
            return
        self._trace.record("note", "worker_started", {"worker_id": worker_id})

    @staticmethod
    def _object(result: str) -> dict[str, object]:
        """Return the reply as an object, empty when it is any other JSON value."""
        reply: object = json.loads(result)
        return cast("dict[str, object]", reply) if isinstance(reply, dict) else {}

    def _receipt(self, call: ClientToolCall, result: str) -> None:
        """Trace a steer's receipt; a held request has none yet."""
        receipt = self._object(result)
        if not receipt or "status" in receipt:
            return
        if receipt.get("choice") in ("approve", "deny"):
            receipt["prompt_seq"] = call.parameters.get("prompt_seq")
        self._trace.record("note", "worker_steer_receipt", receipt)

    def _status(self, result: str) -> None:
        """Trace the worker, state, lifecycle and activity, minus worker text."""
        status = self._object(result)
        if not status:
            return
        activity = status.get("activity")
        if isinstance(activity, dict):
            seen = dict(cast("dict[str, object]", activity))
            if isinstance(failure := seen.get("open_failure"), dict):
                seen["open_failure"] = {
                    "tool": cast("dict[str, object]", failure).get("tool", "")
                }
            activity = seen
        self._trace.record(
            "note",
            "worker_status_result",
            {
                "worker_id": status.get("worker_id"),
                "state": status.get("state"),
                "lifecycle": status.get("lifecycle"),
                "activity": activity,
            },
        )
