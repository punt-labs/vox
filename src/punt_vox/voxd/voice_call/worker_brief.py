"""Spoken-call views of a worker: short enough to read aloud and to seed a prompt."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, cast, final

if TYPE_CHECKING:
    from punt_vox.voxd.workers.types import WorkerPayload

__all__ = ["WorkerBrief"]

# The fields of a hook snapshot a person asks about; the rest is bookkeeping.
_REPORT_FIELDS: Final = (
    "goal",
    "last_assistant_message",
    "open_failure",
    "pending_decision",
)
_CLIP: Final = 400


@final
class WorkerBrief:
    """Project a worker payload to what the voice agent needs to speak about it."""

    __slots__ = ()

    @staticmethod
    def summary(worker: WorkerPayload) -> dict[str, object]:
        """Return the identity, task and state, for lists."""
        return {
            "worker_id": worker["id"],
            "task": WorkerBrief._clip(worker["task"]),
            "project": worker["project"],
            "state": worker["state"],
            "decision_choices": ["approve", "deny"]
            if isinstance(
                pending := worker["last_report"].get("pending_decision"), dict
            )
            and cast("dict[str, object]", pending).get("notification_type")
            == "permission_prompt"
            else [],
        }

    @staticmethod
    def detail(worker: WorkerPayload) -> dict[str, object]:
        """Return the summary plus the worker's latest curated progress."""
        report = worker["last_report"]
        progress = {
            field: WorkerBrief._clip(value) if isinstance(value, str) else value
            for field in _REPORT_FIELDS
            if (value := report.get(field)) is not None
        }
        return {
            **WorkerBrief.summary(worker),
            "branch": worker["branch"],
            "progress": progress,
        }

    @staticmethod
    def _clip(text: str) -> str:
        return text if len(text) <= _CLIP else text[: _CLIP - 1] + "…"
