"""Select the return opener and checkpoint a call's departure boundary."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Self, cast, final

from punt_vox.atomic_file import AtomicFile
from punt_vox.voxd.voice_agent.contracts import SessionOptions
from punt_vox.voxd.voice_call.seed import CallSeed

if TYPE_CHECKING:
    from pathlib import Path

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
        return SessionOptions(
            CallSeed(projects, workers).prompt(),
            tool_ids,
            first_message=self._opening(workers),
        )

    def _opening(self, workers: list[WorkerPayload]) -> str | None:
        """Name the most recent away results and the current worker questions."""
        saved = self._file.read()
        if not saved:
            return None
        cursor = cast("dict[str, int]", json.loads(saved))
        updates: list[str] = []
        for worker in sorted(workers, key=lambda row: row["created_at"], reverse=True):
            if update := self._update(worker, cursor.get(worker["id"], 0)):
                updates.append(update)
            self._covered[worker["id"]] = self._sequence(worker, "worker_seq")
        return " ".join(updates) or None

    def depart(self, workers: list[WorkerPayload]) -> None:
        """Save the report boundary without writing tasks or results again."""
        cursor = {row["id"]: self._sequence(row, "result_seq") for row in workers}
        self._file.replace(json.dumps(cursor) + "\n", mode=0o600)

    @classmethod
    def _update(cls, worker: WorkerPayload, prior: int) -> str:
        report = worker["last_report"]
        snippets: list[str] = []
        result = report.get("last_assistant_message")
        if cls._sequence(worker, "result_seq") > prior and isinstance(result, str):
            snippets.append(f"Result: {cls._speech(result)}")
        pending = report.get("pending_decision")
        if worker["state"] in ("starting", "ready") and isinstance(pending, dict):
            message = cast("dict[str, object]", pending).get("message")
            if isinstance(message, str) and message:
                snippets.append(f"Waiting on: {cls._speech(message)}")
        if not snippets:
            return ""
        return f"For {cls._speech(worker['task'])}: " + " ".join(snippets)

    @staticmethod
    def _speech(text: str) -> str:
        spoken = " ".join(text.split())
        return spoken if len(spoken) <= 160 else spoken[:159] + "…"

    @staticmethod
    def _sequence(worker: WorkerPayload, field: str) -> int:
        value = worker["last_report"].get(field)
        return int(value) if isinstance(value, int) else 0
