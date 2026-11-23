"""Select the return opener and checkpoint a call's departure boundary."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Self, cast, final

from punt_vox.voxd.voice_agent.contracts import SessionOptions
from punt_vox.voxd.voice_call.departure_cursor import DepartureCursor
from punt_vox.voxd.voice_call.seed import CallSeed

if TYPE_CHECKING:
    from punt_vox.voxd.workers.types import WorkerPayload


@final
class ReturnBrief:
    """Use only the provider's curated worker snapshot, never the live event log."""

    __slots__ = ("_covered", "_cursor", "_labels")
    _covered: dict[str, int]
    _cursor: DepartureCursor
    _labels: dict[str, str]

    def __new__(cls, path: Path) -> Self:
        """Bind the durable departure state for one prospective call."""
        self = super().__new__(cls)
        self._cursor = DepartureCursor(path)
        self._covered = {}
        self._labels = {}
        return self

    @property
    def covered(self) -> dict[str, int]:
        """Return the per-worker live stream cut."""
        return self._covered

    @property
    def labels(self) -> dict[str, str]:
        """Return the call's spoken aliases shared with worker tools."""
        return self._labels

    def options(
        self,
        workers: list[WorkerPayload],
        projects: tuple[str, ...],
        tool_ids: tuple[str, ...],
    ) -> SessionOptions:
        """Build the call prompt and opener from the same worker snapshot."""
        self._covered.update(
            (worker["id"], self._sequence(worker, "worker_seq")) for worker in workers
        )
        opening, labels = self._opening(workers)
        self._labels.update(
            (label.casefold(), worker_id) for label, worker_id in labels.items()
        )
        return SessionOptions(
            CallSeed(projects, workers).prompt(labels),
            tool_ids,
            first_message=opening,
        )

    def _opening(
        self, workers: list[WorkerPayload]
    ) -> tuple[str | None, dict[str, str]]:
        """Name the most recent away results and the current worker questions."""
        baseline = ((w["id"], self._sequence(w, "result_seq")) for w in workers)
        if not self._cursor.load(baseline):
            return None, {}
        updates: list[tuple[str, str, str]] = []
        for worker in sorted(workers, key=lambda row: row["created_at"], reverse=True):
            worker_id = worker["id"]
            update, result = self._update(worker, self._cursor.sequence(worker_id))
            if update:
                project = Path(worker["project"]).name or "the project"
                updates.append((worker_id, project, update))
            self._cursor.record(worker_id, result)
        return self._labelled_updates(updates)

    @staticmethod
    def _labelled_updates(
        updates: list[tuple[str, str, str]],
    ) -> tuple[str | None, dict[str, str]]:
        counts = Counter(project.casefold() for _, project, _ in updates)
        ordinals: Counter[str] = Counter()
        labels: dict[str, str] = {}
        spoken: list[str] = []
        for worker_id, project, update in updates:
            key = project.casefold()
            if counts[key] == 1:
                label = f"The worker in {project}"
            else:
                ordinals[key] += 1
                label = f"Worker {ordinals[key]} in {project}"
            labels[label] = worker_id
            spoken.append(f"{label} {update}")
        if not spoken:
            return None, labels
        opening = " ".join(spoken)
        return f"While you were away, {opening[:1].lower()}{opening[1:]}", labels

    def depart(self, reported: dict[str, int]) -> None:
        """Persist only baseline or submitted results, never a late snapshot."""
        self._cursor.save(reported)

    @classmethod
    def _update(cls, worker: WorkerPayload, prior: int) -> tuple[str, int]:
        snippets: list[str] = []
        result = worker["last_report"].get("last_assistant_message")
        sequence = cls._sequence(worker, "result_seq")
        if sequence > prior and isinstance(result, str):
            snippets.append(f"finished: {cls._speech(result)}")
        else:
            sequence = 0
        if question := cls._pending_question(worker):
            subject = "It " if snippets else ""
            snippets.append(f"{subject}needs your input: {cls._speech(question)}")
        return " ".join(snippets), sequence

    @staticmethod
    def _pending_question(worker: WorkerPayload) -> str:
        pending = worker["last_report"].get("pending_decision")
        if worker["state"] not in ("starting", "ready") or not isinstance(
            pending, dict
        ):
            return ""
        message = cast("dict[str, object]", pending).get("message")
        return message if isinstance(message, str) else ""

    @staticmethod
    def _speech(text: str) -> str:
        spoken = " ".join(text.split())
        if len(spoken) > 160:
            return spoken[:159] + "…"
        return spoken if spoken.endswith((".", "?", "!", "…")) else spoken + "."

    @staticmethod
    def _sequence(worker: WorkerPayload, field: str) -> int:
        value = worker["last_report"].get(field)
        return int(value) if isinstance(value, int) else 0
