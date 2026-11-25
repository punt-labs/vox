"""One serialized actor over the registry, shared by worker RPCs, hooks and calls."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.workers.codec import WorkerRecordCodec
from punt_vox.voxd.workers.hooks import WorkerHooks
from punt_vox.voxd.workers.steering import WorkerSteering
from punt_vox.voxd.workers.types import WorkerDeadlines

if TYPE_CHECKING:
    from concurrent.futures import Future

    from punt_vox.voxd.hook_context_store import HookContextStore
    from punt_vox.voxd.voice_call.progress import ProgressEvents
    from punt_vox.voxd.workers.model import WorkerRecord
    from punt_vox.voxd.workers.registry import WorkerRegistry
    from punt_vox.voxd.workers.steering_types import DecisionChoice, SteerReceipt
    from punt_vox.voxd.workers.types import WorkerPayload

logger = logging.getLogger(__name__)


@final
class WorkerActor:
    """Serialize mutations while running blocking provider I/O off the event loop."""

    __slots__ = ("_lock", "_registry", "_steering", "_store")
    _registry: WorkerRegistry
    _lock: asyncio.Lock
    _steering: WorkerSteering
    _store: HookContextStore | None  # PY-TS-14: standalone actors have no hooks.

    def __new__(
        cls,
        registry: WorkerRegistry,
        store: HookContextStore | None = None,  # PY-TS-14: standalone actors omit it.
    ) -> Self:
        """Bind one registry and its serialization lock."""
        self = super().__new__(cls)
        self._registry = registry
        self._store = store
        if store is not None:
            registry.release_with(store.release_worker)
        self._lock = asyncio.Lock()
        self._steering = WorkerSteering(registry, self._lock)
        return self

    async def start(
        self,
        task: str,
        project: str,
        *,
        provider: str = "tmux",
        harness: str = "claude",
    ) -> WorkerPayload:
        """Start one persistent worker in an allowlisted project.

        Every worker RPC holds the lock while it runs, so a start queued
        behind another call waits at most ``WorkerDeadlines.LOCK_WAIT`` and
        is then refused with the reason; the client's budget covers one
        start, not a start behind a slow call.
        """
        await self._acquire_for_start(project)
        try:
            record = await asyncio.to_thread(
                self._registry.start, task, project, provider=provider, harness=harness
            )
        finally:
            self._lock.release()
        return WorkerRecordCodec.public(record)

    async def _acquire_for_start(self, project: str) -> None:
        """Take the actor lock within the start bound or refuse with the reason.

        The lock may be held by any worker call, not only a start, so the
        refusal names the controller, and the daemon log names the project
        by its last path component, as the worktree log does.
        """
        try:
            await asyncio.wait_for(self._lock.acquire(), WorkerDeadlines.LOCK_WAIT)
        except TimeoutError:
            logger.warning(
                "Worker start for %s refused: the controller was busy for %s s",
                Path(project).name,
                WorkerDeadlines.LOCK_WAIT,
            )
            raise ValueError(
                "the worker controller is busy; try again once it is free"
            ) from None

    async def stop(self, worker_id: str) -> WorkerPayload:
        """Stop one worker; its worktree and branch are retained.

        The reply waits only for the job the registry hands back: harness
        cleanup. A retried discard is queued and reported pending instead.
        """
        async with self._lock:
            record, cleanup = await asyncio.to_thread(
                self._registry.request_stop, worker_id
            )
            if self._store is not None:
                self._store.release_worker(worker_id)
        return WorkerRecordCodec.public(await self._settled(record, cleanup))

    async def _settled(
        self,
        record: WorkerRecord,
        cleanup: Future[None] | None,  # PY-TS-14: no job means nothing to wait on.
    ) -> WorkerRecord:
        """Wait for the stop's own cleanup job, off the lock, then reread."""
        if cleanup is None:
            return record
        await asyncio.shield(asyncio.wrap_future(cleanup))
        return self._registry.hook_records.get(record.id)

    def shutdown(self) -> None:
        """Close the worker subsystem's cleanup queue."""
        self._registry.shutdown()

    async def workers(self) -> list[WorkerPayload]:
        """Reconcile and return every known worker."""
        async with self._lock:
            records = await asyncio.to_thread(self._registry.list)
        return [WorkerRecordCodec.public(record) for record in records]

    async def steer(
        self,
        worker_id: str,
        words: str,
        *,
        urgent: bool,
        prompt_epoch: int
        | None = None,  # None binds direct control to the live prompt.
        choice: DecisionChoice | None = None,  # None submits ordinary follow-up text.
        prompt_seq: int
        | None = None,  # None is ordinary text, not a permission answer.
    ) -> SteerReceipt:
        """Deliver words verbatim and report whether the worker heard them."""
        return await self.steer_request(
            {
                "worker_id": worker_id,
                "words": words,
                "urgent": urgent,
                "choice": choice,
                "prompt_epoch": prompt_epoch,
                "prompt_seq": prompt_seq,
            }
        )

    async def steer_request(self, msg: dict[str, object]) -> SteerReceipt:
        """Validate and deliver one wire steering request."""
        return await self._steering.handle(msg)

    def hooks(
        self, store: HookContextStore, events: ProgressEvents | None = None
    ) -> WorkerHooks:
        """Return hook ingestion that checkpoints under this actor's lock."""
        return WorkerHooks(store, self._registry, self._lock, events)
