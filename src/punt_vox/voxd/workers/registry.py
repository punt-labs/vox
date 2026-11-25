"""Provider-neutral worker lifecycle with restart reconciliation."""

from __future__ import annotations

import logging
import subprocess
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Self, cast, final
from uuid import uuid4

from punt_vox.voxd.hook_snapshot import WorkerHookCheckpoint
from punt_vox.voxd.workers.contracts import HarnessProfile, WorkerLaunch, WorkerProvider
from punt_vox.voxd.workers.inventory import WorkerInventory
from punt_vox.voxd.workers.lifecycle import WorkerLifecycle
from punt_vox.voxd.workers.model import WorkerRecord
from punt_vox.voxd.workers.policy import WorkerRepoPolicy
from punt_vox.voxd.workers.records import WorkerRecords
from punt_vox.voxd.workers.retirement import WorkerRetirement
from punt_vox.voxd.workers.steer_delivery import SteerDelivery
from punt_vox.voxd.workers.steering_types import DecisionChoice

if TYPE_CHECKING:
    from collections.abc import Callable
    from concurrent.futures import Future

logger = logging.getLogger(__name__)


@final
class WorkerRegistry:
    """Resolve providers and harnesses by name, never by special-case branches."""

    __slots__ = (
        "_inventory",
        "_lifecycle",
        "_policy",
        "_profiles",
        "_providers",
        "_retirement",
    )
    _inventory: WorkerInventory
    _lifecycle: WorkerLifecycle
    _policy: WorkerRepoPolicy
    _profiles: dict[str, HarnessProfile]
    _providers: dict[str, WorkerProvider]
    _retirement: WorkerRetirement

    def __new__(
        cls,
        policy: WorkerRepoPolicy,
        records: WorkerRecords,
        providers: dict[str, WorkerProvider],
        profiles: dict[str, HarnessProfile],
    ) -> Self:
        """Wire an empty registry for startup recovery."""
        self = super().__new__(cls)
        self._policy = policy
        self._providers, self._profiles = providers, profiles
        self._lifecycle = WorkerLifecycle(providers, profiles)
        self._inventory = WorkerInventory(records)
        self._retirement = WorkerRetirement.for_registry(
            profiles, policy, self._inventory
        )
        return self

    def restore(
        self,
        records: list[WorkerRecord],
        *,
        unconfirmed: set[str] | None = None,  # PY-TS-14: omit without a probe pass.
    ) -> None:
        """Install reconciled checkpoints before admitting callers."""
        self._inventory.restore(records, unconfirmed=unconfirmed)
        for record in records:
            self._retirement.schedule(record.id)

    def cleanup_idle(self) -> bool:
        """Check whether any worker cleanup remains in flight."""
        return self._retirement.cleanup_idle()

    def shutdown(self) -> None:
        """Bound the final wait for worker retirement cleanup."""
        self._retirement.shutdown()

    def release_with(self, release: Callable[[str], None]) -> None:
        """Release a confirmed exit's retained context on either control path."""
        self._retirement.release_with(release)

    @property
    def hook_records(self) -> WorkerInventory:
        """Expose admission and receipt lookup without exposing worker control."""
        return self._inventory

    def start(
        self,
        task: str,
        project: str,
        *,
        provider: str = "tmux",
        harness: str = "claude",
    ) -> WorkerRecord:
        """Validate all inputs before making any worktree or launching a process."""
        self._validate(task, provider, harness)
        repo = self._policy.resolve(project)
        worker_id = str(uuid4())
        worktree = self._inventory.worktree(worker_id)
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
        self._inventory.save(record)
        profile = self._profiles[harness]
        try:
            self._policy.create(repo, worktree, branch)
            profile.prepare(worktree)
            self._providers[provider].start(
                WorkerLaunch(
                    handle, worker_id, worktree, task, self._inventory.namespace
                ),
                profile,
            )
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            self._rollback(record, exc)
            raise
        observed, _ = self._lifecycle.observe(record)
        self._inventory.save(observed)
        self._retirement.schedule(observed.id)
        return observed

    def _validate(self, task: str, provider: str, harness: str) -> None:
        """Refuse an empty task or a provider or harness the catalogs lack."""
        if not task.strip():
            raise ValueError("Worker task must not be empty")
        if provider not in self._providers or harness not in self._profiles:
            raise ValueError("Unknown worker provider or harness")

    def _rollback(self, record: WorkerRecord, error: Exception) -> None:
        """Record the failure now; the worktree discard runs off the lock."""
        record = self._lifecycle.rollback(record, error)
        try:
            self._inventory.save(record)
        except (OSError, ValueError, RuntimeError) as exc:
            self._inventory.remember(record)
            logger.warning(
                "Worker %s rollback checkpoint not saved (%s)",
                record.id,
                type(exc).__name__,
            )
        self._retirement.schedule(record.id)

    def list(self) -> list[WorkerRecord]:
        """Report current state and close hook admission for confirmed exits."""
        return [
            self._retirement.observe(record, self._lifecycle)
            for record in self._inventory.registered()
        ]

    def admits_hook(self, worker_id: object, cwd: object) -> bool:
        """Check that a worker accepts active hook traffic from this worktree."""
        return self._inventory.match(worker_id, cwd, None) is not None

    def checkpoint_progress(self, record: WorkerRecord) -> None:
        """Persist curated progress before a managed hook delivery is acknowledged."""
        self._inventory.save(self._lifecycle.report(record))

    def checkpoint_exit(
        self, record: WorkerRecord, checkpoint: WorkerHookCheckpoint
    ) -> None:
        """Update terminal progress without changing phase or cleanup markers."""
        self._inventory.update(
            record.id,
            lambda current: replace(
                current, last_report={**current.last_report, **checkpoint}
            ),
        )

    def stop(self, worker_id: str) -> WorkerRecord:
        """Terminate a worker and settle every debt it owes before returning."""
        record = self._inventory.update(worker_id, self._lifecycle.terminate)
        job = self._retirement.schedule(worker_id)
        if job is None:
            return record
        job.result()
        return self._inventory.get(worker_id)

    def request_stop(
        self, worker_id: str
    ) -> tuple[WorkerRecord, Future[None] | None]:  # PY-TS-14: only wait on a new job.
        """Persist termination and queue cleanup; return a job a reply may wait on.

        A retried worktree discard is queued but withheld: the reply reports
        ``discard_pending`` and the outcome lands on the record later.
        """
        record = self._inventory.update(worker_id, self._lifecycle.terminate)
        return record, self._retirement.schedule_prompt(worker_id)

    def steer(
        self,
        worker_id: str,
        words: str,
        *,
        urgent: bool = False,
        choice: DecisionChoice | None = None,  # None submits ordinary follow-up text.
        prompt_epoch: int
        | None = None,  # None binds direct control to the live prompt.
        prompt_seq: int
        | None = None,  # None is ordinary text, not a permission answer.
    ) -> int:
        """Delegate steering without knowing the harness's keys or transport."""
        SteerDelivery.validate(words)
        record = self._retirement.observe(
            self._inventory.get(worker_id), self._lifecycle
        )
        if record.state != "ready":
            raise ValueError(f"Worker {worker_id} is not ready ({record.state})")
        self._inventory.save(record)
        if choice is not None:
            self._question(record, prompt_seq)
        if prompt_epoch is not None:
            self._live_prompt(record, prompt_epoch)
        return SteerDelivery(
            self._profiles[record.harness], self._providers[record.provider]
        ).send(record, words, urgent=urgent, choice=choice)

    @staticmethod
    def _live_prompt(record: WorkerRecord, prompt_epoch: int) -> None:
        """Refuse steering bound to a prompt the worker has since replaced."""
        if prompt_epoch != record.last_report.get("prompt_epoch", 0):
            raise ValueError(
                "Worker has a new prompt; read worker_status and ask the person "
                "again before steering"
            )

    @staticmethod
    def _question(
        record: WorkerRecord,
        prompt_seq: int | None,  # None cannot identify a permission question.
    ) -> None:
        if (
            isinstance(prompt_seq, bool)
            or not isinstance(prompt_seq, int)
            or prompt_seq <= 0
        ):
            raise ValueError(
                "choice requires prompt_seq from the announced permission prompt"
            )
        pending = record.last_report.get("pending_decision")
        if (
            not isinstance(pending, dict)
            or cast("dict[str, object]", pending).get("notification_type")
            != "permission_prompt"
            or cast("dict[str, object]", pending).get("seq") != prompt_seq
        ):
            raise ValueError(
                "worker has a different prompt now; read worker_status and "
                "ask the person again"
            )

    # PY-TS-14: absence means no later input matches this steering request yet.
    def receipt(self, worker_id: str, words: str, baseline: int) -> int | None:
        """Recognize a later matching input without inferring turn completion."""
        profile = self._profiles[self._inventory.get(worker_id).harness]
        for receipt in profile.inputs_since(worker_id, baseline):
            if receipt.seq > baseline and profile.matches_input(receipt, words):
                return receipt.seq
        return None
