"""Provider-neutral worker lifecycle with restart reconciliation."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Self, final
from uuid import uuid4

from punt_vox.voxd.workers.contracts import HarnessProfile, WorkerProvider
from punt_vox.voxd.workers.policy import WorkerRepoPolicy
from punt_vox.voxd.workers.records import WorkerRecords


@final
class WorkerRegistry:
    """Resolve providers and harnesses by name, never by special-case branches."""

    __slots__ = ("_policy", "_profiles", "_providers", "_records", "_workers")
    _policy: WorkerRepoPolicy
    _profiles: dict[str, HarnessProfile]
    _providers: dict[str, WorkerProvider]
    _records: WorkerRecords
    _workers: dict[str, dict[str, object]]

    def __new__(
        cls,
        policy: WorkerRepoPolicy,
        records: WorkerRecords,
        providers: dict[str, WorkerProvider],
        profiles: dict[str, HarnessProfile],
    ) -> Self:
        """Rebuild persistent workers using the supplied provider catalogs."""
        self = super().__new__(cls)
        self._policy, self._records = policy, records
        self._providers, self._profiles = providers, profiles
        self._workers = {str(r["id"]): r for r in records.load()}
        self.list()
        return self

    def start(
        self,
        task: str,
        project: str,
        *,
        provider: str = "tmux",
        harness: str = "claude",
    ) -> dict[str, object]:
        """Validate all inputs before making any worktree or launching a process."""
        if not task.strip():
            raise ValueError("Worker task must not be empty")
        if provider not in self._providers or harness not in self._profiles:
            raise ValueError("Unknown worker provider or harness")
        repo = self._policy.resolve(project)
        worker_id = str(uuid4())
        worktree = self._records.worktree(worker_id)
        branch = f"vox-worker/{worker_id}"
        handle = f"vox-worker-{worker_id}"
        self._policy.create(repo, worktree, branch)
        record: dict[str, object] = {
            "id": worker_id,
            "provider": provider,
            "harness": harness,
            "session": handle,
            "project": str(repo),
            "worktree": str(worktree),
            "branch": branch,
            "task": task,
            "state": "starting",
            "created_at": datetime.now(UTC).isoformat(),
            "last_report": {},
        }
        self._workers[worker_id] = record
        self._records.save(record)
        profile = self._profiles[harness]
        try:
            profile.prepare(worktree)
            self._providers[provider].start(handle, worker_id, worktree, task, profile)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            self.stop(worker_id)
            raise
        return self._refresh(record)

    def _refresh(self, record: dict[str, object]) -> dict[str, object]:
        if record["state"] not in ("stopped", "exited"):
            provider = self._providers[str(record["provider"])]
            profile = self._profiles[str(record["harness"])]
            if not provider.status(str(record["session"])):
                record["state"] = "exited"
                profile.cleanup(Path(str(record["worktree"])))
            else:
                report = provider.progress(str(record["id"]), profile)
                record["last_report"] = report
                record["state"] = (
                    "ready" if report.get("session_started") else "starting"
                )
            self._records.save(record)
        return dict(record)

    def list(self) -> list[dict[str, object]]:
        """Reconcile live state, including after a daemon restart."""
        return [self._refresh(r) for r in self._workers.values()]

    def stop(self, worker_id: str) -> dict[str, object]:
        """Terminate a worker but preserve its branch and worktree."""
        record = self._workers[worker_id]
        self._providers[str(record["provider"])].stop(str(record["session"]))
        self._profiles[str(record["harness"])].cleanup(Path(str(record["worktree"])))
        record["state"] = "stopped"
        self._records.save(record)
        return dict(record)

    def steer(self, worker_id: str, words: str, *, urgent: bool = False) -> None:
        """Delegate steering without knowing the harness's keys or transport."""
        record = self._workers[worker_id]
        self._providers[str(record["provider"])].steer(
            str(record["session"]),
            words,
            urgent=urgent,
            profile=self._profiles[str(record["harness"])],
        )
