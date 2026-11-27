"""Trace what a tool told the agent, keeping prompts and worker text out."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Self, cast, final

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
        """Trace a steer's receipt or a worker_status reading; others leave none."""
        if call.tool_name == "steer_worker":
            self._receipt(call, result)
        elif call.tool_name == "worker_status":
            self._status(result)

    def _receipt(self, call: ClientToolCall, result: str) -> None:
        """Trace a steer's receipt; a held request has none yet."""
        receipt: dict[str, object] = json.loads(result)
        if "status" in receipt:
            return
        if receipt.get("choice") in ("approve", "deny"):
            receipt["prompt_seq"] = call.parameters.get("prompt_seq")
        self._trace.record("note", "worker_steer_receipt", receipt)

    def _status(self, result: str) -> None:
        """Trace the state, lifecycle and activity, without worker-written text."""
        status: dict[str, object] = json.loads(result)
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
                "state": status.get("state"),
                "lifecycle": status.get("lifecycle"),
                "activity": activity,
            },
        )
