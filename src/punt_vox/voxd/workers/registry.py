"""Provider-neutral worker lifecycle with restart reconciliation."""

from __future__ import annotations

import logging
import subprocess
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Self, final
from uuid import uuid4

from punt_vox.voxd.workers.contracts import HarnessProfile, WorkerLaunch, WorkerProvider
from punt_vox.voxd.workers.model import WorkerRecord
from punt_vox.voxd.workers.policy import WorkerRepoPolicy
from punt_vox.voxd.workers.records import WorkerRecords

logger = logging.getLogger(__name__)


@final
class WorkerRegistry:
    """Resolve providers and harnesses by name, never by special-case branches."""

    __slots__ = ("_policy", "_profiles", "_providers", "_records", "_workers")
    _policy: WorkerRepoPolicy
    _profiles: dict[str, HarnessProfile]
    _providers: dict[str, WorkerProvider]
    _records: WorkerRecords
    _workers: dict[str, WorkerRecord]

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
        self._workers = {r.id: r for r in records.load()}
        self.list()
        return self

    def start(
        self,
        task: str,
        project: str,
        *,
        provider: str = "tmux",
        harness: str = "claude",
    ) -> WorkerRecord:
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
        record = WorkerRecord(
            id=worker_id,
            provider=provider,
            harness=harness,
            session=handle,
            project=str(repo),
            worktree=str(worktree),
            branch=branch,
            task=task,
            state="starting",
            created_at=datetime.now(UTC).isoformat(),
            last_report={},
        )
        self._records.save(record)
        self._workers[worker_id] = record
        profile = self._profiles[harness]
        try:
            self._policy.create(repo, worktree, branch)
            profile.prepare(worktree)
            self._providers[provider].start(
                WorkerLaunch(handle, worker_id, worktree, task), profile
            )
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            self.stop(worker_id)
            raise
        return self._refresh(record)

    def _refresh(self, record: WorkerRecord) -> WorkerRecord:
        if record.state not in ("stopped", "exited"):
            provider = self._providers[record.provider]
            profile = self._profiles[record.harness]
            try:
                alive = provider.status(record.session)
            except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                logger.warning("Worker provider unavailable (%s)", type(exc).__name__)
                return record
            if not alive:
                record = self._cleanup(replace(record, state="exited"))
            else:
                report = provider.progress(record.id, profile)
                if record.state != "ready" or (
                    report and report != {"session_started": False}
                ):
                    record = replace(
                        record,
                        last_report=report,
                        state="ready"
                        if report.get("session_started")
                        else record.state,
                    )
            self._records.save(record)
            self._workers[record.id] = record
        return record

    def _cleanup(self, record: WorkerRecord) -> WorkerRecord:
        try:
            self._profiles[record.harness].cleanup(Path(record.worktree))
        except (OSError, ValueError, RuntimeError) as exc:
            record = replace(
                record,
                last_report={**record.last_report, "cleanup_error": type(exc).__name__},
            )
            logger.warning("Worker cleanup failed (%s)", type(exc).__name__)
        return record

    def list(self) -> list[WorkerRecord]:
        """Reconcile live state, including after a daemon restart."""
        return [self._refresh(r) for r in self._workers.values()]

    def _get(self, worker_id: str) -> WorkerRecord:
        try:
            return self._workers[worker_id]
        except KeyError:
            raise ValueError(f"no worker {worker_id}") from None

    def stop(self, worker_id: str) -> WorkerRecord:
        """Terminate a worker but preserve its branch and worktree."""
        record = self._get(worker_id)
        if record.state in ("stopped", "exited"):
            return record
        self._providers[record.provider].stop(record.session)
        record = self._cleanup(replace(record, state="stopped"))
        self._records.save(record)
        self._workers[record.id] = record
        return record

    def steer(self, worker_id: str, words: str, *, urgent: bool = False) -> None:
        """Delegate steering without knowing the harness's keys or transport."""
        record = self._get(worker_id)
        self._providers[record.provider].steer(
            record.session,
            words,
            urgent=urgent,
            profile=self._profiles[record.harness],
        )
