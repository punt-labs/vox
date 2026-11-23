"""Provider-neutral worker lifecycle with restart reconciliation."""

from __future__ import annotations

import logging
import subprocess
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Self, cast, final
from uuid import uuid4

from punt_vox.voxd.workers.contracts import HarnessProfile, WorkerLaunch, WorkerProvider
from punt_vox.voxd.workers.lifecycle import WorkerLifecycle
from punt_vox.voxd.workers.model import WorkerRecord
from punt_vox.voxd.workers.policy import WorkerRepoPolicy
from punt_vox.voxd.workers.records import WorkerRecords
from punt_vox.voxd.workers.steer_delivery import SteerDelivery
from punt_vox.voxd.workers.steering_types import DecisionChoice

logger = logging.getLogger(__name__)


@final
class WorkerRegistry:
    """Resolve providers and harnesses by name, never by special-case branches."""

    __slots__ = (
        "_lifecycle",
        "_policy",
        "_profiles",
        "_providers",
        "_records",
        "_workers",
    )
    _lifecycle: WorkerLifecycle
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
        self._lifecycle = WorkerLifecycle(providers, profiles)
        self._workers = {r.id: r for r in records.load(strict=False)}
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
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            self._rollback(record, exc)
            raise
        return self._checkpoint(self._lifecycle.observe(record))

    def _checkpoint(self, record: WorkerRecord) -> WorkerRecord:
        if self._workers.get(record.id) == record:
            return record
        self._records.save(record)
        self._workers[record.id] = record
        return record

    def _rollback(self, record: WorkerRecord, error: Exception) -> None:
        record = self._lifecycle.rollback(record, error)
        try:
            self._checkpoint(record)
        except (OSError, ValueError, RuntimeError) as exc:
            self._workers[record.id] = record
            logger.warning("Worker rollback checkpoint failed (%s)", type(exc).__name__)

    def list(self) -> list[WorkerRecord]:
        """Reconcile live state, including after a daemon restart."""
        result: list[WorkerRecord] = []
        for record in self._workers.values():
            try:
                result.append(self._checkpoint(self._lifecycle.observe(record)))
            except (
                KeyError,
                OSError,
                ValueError,
                RuntimeError,
                subprocess.SubprocessError,
            ) as exc:
                logger.warning(
                    "Worker %s reconciliation unavailable (%s)",
                    record.id,
                    type(exc).__name__,
                )
                result.append(record)
        return result

    def _get(self, worker_id: str) -> WorkerRecord:
        try:
            return self._workers[worker_id]
        except KeyError:
            raise ValueError(f"no worker {worker_id}") from None

    def admits_hook(self, worker_id: object, cwd: object) -> bool:
        """Accept only an active worker's exact session and canonical worktree."""
        if not isinstance(worker_id, str) or not isinstance(cwd, str) or not cwd:
            return False
        record = self._workers.get(worker_id)
        return (
            record is not None
            and record.state in ("starting", "ready")
            and "launch_error" not in record.last_report
            and Path(cwd).is_absolute()
            and record.worktree == str(Path(cwd).resolve())
        )

    def checkpoint_progress(self, worker_id: object, cwd: object) -> None:
        """Persist curated progress before a managed hook delivery is acknowledged."""
        if self.admits_hook(worker_id, cwd):
            self._checkpoint(
                self._lifecycle.report(self._workers[cast("str", worker_id)])
            )

    def stop(self, worker_id: str) -> WorkerRecord:
        """Terminate a worker but preserve its branch and worktree."""
        return self._checkpoint(self._lifecycle.stop(self._get(worker_id)))

    def steer(
        self,
        worker_id: str,
        words: str,
        *,
        urgent: bool = False,
        choice: DecisionChoice | None = None,  # None submits ordinary follow-up text.
        prompt_epoch: int
        | None = None,  # None binds direct control to the live prompt.
    ) -> int:
        """Delegate steering without knowing the harness's keys or transport."""
        if not words.strip():
            raise ValueError("Worker words must not be empty or whitespace")
        if any(unicodedata.category(c) in ("Cc", "Zl", "Zp") for c in words):
            raise ValueError(
                "Worker words must not contain control characters or newlines"
            )
        record = self._checkpoint(self._lifecycle.observe(self._get(worker_id)))
        if record.state != "ready":
            raise ValueError(f"Worker {worker_id} is not ready ({record.state})")
        if prompt_epoch is not None and prompt_epoch != record.last_report.get(
            "prompt_epoch", 0
        ):
            raise ValueError(
                "Worker has a new prompt; read worker_status and ask the person "
                "again before steering"
            )
        profile = self._profiles[record.harness]
        if len(words) > profile.input_limit:
            raise ValueError(
                f"Words exceed receipt limit of {profile.input_limit} characters"
            )
        return SteerDelivery(profile, self._providers[record.provider]).send(
            record, words, urgent=urgent, choice=choice
        )

    # PY-TS-14: absence means no later input matches this steering request yet.
    def receipt(self, worker_id: str, words: str, baseline: int) -> int | None:
        """Recognize a later matching input without inferring turn completion."""
        profile = self._profiles[self._get(worker_id).harness]
        for receipt in profile.inputs_since(worker_id, baseline):
            if receipt.seq > baseline and profile.matches_input(receipt, words):
                return receipt.seq
        return None
