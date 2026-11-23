"""Select the return opener and checkpoint a call's departure boundary."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Self, cast, final

from punt_vox.atomic_file import AtomicFile
from punt_vox.voxd.voice_agent.contracts import SessionOptions
from punt_vox.voxd.voice_call.seed import CallSeed

if TYPE_CHECKING:
    from punt_vox.voxd.workers.types import WorkerPayload


@final
class ReturnBrief:
    """Use only the provider's curated worker snapshot, never the live event log."""

    __slots__ = ("_covered", "_file")
    _file: AtomicFile
    _covered: dict[str, int]

    def __new__(cls, path: Path) -> Self:
        """Bind the durable departure state for one prospective call."""
        self = super().__new__(cls)
        self._file = AtomicFile(path)
        self._covered = {}
        return self

    @property
    def covered(self) -> dict[str, int]:
        """Return the per-worker live stream cut."""
        return self._covered

    def options(
        self,
        workers: list[WorkerPayload],
        projects: tuple[str, ...],
        tool_ids: tuple[str, ...],
    ) -> SessionOptions:
        """Build the call prompt and opener from the same worker snapshot."""
        opening, labels = self._opening(workers)
        return SessionOptions(
            CallSeed(projects, workers).prompt(labels),
            tool_ids,
            first_message=opening,
        )

    def _opening(
        self, workers: list[WorkerPayload]
    ) -> tuple[str | None, dict[str, str]]:
        """Name the most recent away results and the current worker questions."""
        saved = self._file.read()
        if not saved:
            return None, {}
        cursor = cast("dict[str, int]", json.loads(saved))
        updates: list[tuple[str, str, str]] = []
        for worker in sorted(workers, key=lambda row: row["created_at"], reverse=True):
            if update := self._update(worker, cursor.get(worker["id"], 0)):
                project = Path(worker["project"]).name or "the project"
                updates.append((worker["id"], project, update))
            self._covered[worker["id"]] = self._sequence(worker, "worker_seq")
        return self._labelled_updates(updates)

    @staticmethod
    def _labelled_updates(
        updates: list[tuple[str, str, str]],
    ) -> tuple[str | None, dict[str, str]]:
        counts = Counter(project for _, project, _ in updates)
        ordinals: Counter[str] = Counter()
        labels: dict[str, str] = {}
        spoken: list[str] = []
        for worker_id, project, update in updates:
            if counts[project] == 1:
                label = f"The worker in {project}"
            else:
                ordinals[project] += 1
                label = f"Worker {ordinals[project]} in {project}"
                labels[label] = worker_id
            spoken.append(f"{label} {update}")
        if not spoken:
            return None, labels
        opening = " ".join(spoken)
        return f"While you were away, {opening[:1].lower()}{opening[1:]}", labels

    def depart(self, workers: list[WorkerPayload]) -> None:
        """Save the report boundary without writing tasks or results again."""
        cursor = {row["id"]: self._sequence(row, "result_seq") for row in workers}
        self._file.replace(json.dumps(cursor) + "\n", mode=0o600)

    @classmethod
    def _update(cls, worker: WorkerPayload, prior: int) -> str:
        snippets: list[str] = []
        result = worker["last_report"].get("last_assistant_message")
        if cls._sequence(worker, "result_seq") > prior and isinstance(result, str):
            snippets.append(f"finished: {cls._speech(result)}")
        if question := cls._pending_question(worker):
            subject = "It " if snippets else ""
            snippets.append(f"{subject}needs your input: {cls._speech(question)}")
        return " ".join(snippets)

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
