"""What a worker is doing right now, read from its checkpointed hook report."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Literal, Self, cast, final

if TYPE_CHECKING:
    from punt_vox.voxd.workers.types import WorkerPayload

__all__ = ["ActivityState", "WorkerActivity"]

# Report keys set when launch, stop or cleanup failed: nothing more will happen.
_ERROR_KEYS: Final = ("launch_error", "stop_error", "cleanup_error")

# The state words the voice agent speaks; the registry's launch phase is not one.
type ActivityState = Literal["working", "blocked", "idle", "finished"]


@final
class WorkerActivity:
    """Read one worker's hook-derived state, tool, files and failure.

    Every field comes from the report voxd checkpoints as hook events arrive,
    so nothing here reads the worker's terminal.
    """

    __slots__ = ("_lifecycle", "_report")
    _lifecycle: str
    _report: dict[str, object]  # PY-TS-14: extensible harness JSON at the edge.

    def __new__(cls, worker: WorkerPayload) -> Self:
        """Bind the worker's launch phase and its latest hook report."""
        self = super().__new__(cls)
        self._lifecycle = worker["state"]
        self._report = worker["last_report"]
        return self

    @property
    def state(self) -> ActivityState:
        """Return blocked on a permission question, finished once a turn has ended.

        A Stop ends the turn; the word stays ``finished`` until a new prompt or
        tool starts, whatever lifecycle events follow.
        """
        if self._lifecycle in ("exited", "stopped") or any(
            key in self._report for key in _ERROR_KEYS
        ):
            return "finished"
        pending = self._report.get("pending_decision")
        if isinstance(pending, dict):
            return self._asked(cast("dict[str, object]", pending))
        if self._report.get("in_flight_tool"):
            return "working"
        if not self._count("worker_seq"):
            return "idle"
        return "finished" if self._turn_ended() else "working"

    def _asked(self, pending: dict[str, object]) -> ActivityState:
        """Return the word for an open question: blocked, or waiting for a prompt."""
        if pending.get("notification_type") != "idle_prompt":
            return "blocked"
        return "finished" if self._count("turn_seq") else "idle"

    def _turn_ended(self) -> bool:
        """Return whether no prompt or tool began after the last Stop or question."""
        ended = self._count("prompt_epoch")
        return bool(ended) and self._count("turn_seq") <= ended

    def _count(self, field: str) -> int:
        """Return a sequence field of the report, 0 when absent or not a number."""
        value = self._report.get(field, 0)
        return value if isinstance(value, int) else 0

    @property
    def decision_choices(self) -> list[str]:
        """Return the answers the person may give to a pending permission prompt."""
        pending = self._report.get("pending_decision")
        if (
            isinstance(pending, dict)
            and cast("dict[str, object]", pending).get("notification_type")
            == "permission_prompt"
        ):
            return ["approve", "deny"]
        return []

    def detail(self, now: float) -> dict[str, object]:
        """Return the tool, files, open failure and silence, omitting what is unseen."""
        files = self._report.get("active_files")
        detail: dict[str, object] = {
            "files": [f for f in cast("list[object]", files) if isinstance(f, str)]
            if isinstance(files, list)
            else []
        }
        if tool := self._report.get("in_flight_tool"):
            detail["tool_in_flight"] = tool
        if isinstance(failure := self._report.get("open_failure"), dict):
            found = cast("dict[str, object]", failure)
            detail["open_failure"] = {
                "tool": found.get("tool", ""),
                "result": found.get("result", ""),
            }
        heard = self._report.get("last_event_at")
        if isinstance(heard, int | float) and 0 < heard <= now:
            detail["seconds_since_last_event"] = int(now - heard)
        return detail
