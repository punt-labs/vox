"""One serialized actor over the registry, shared by worker RPCs, hooks and calls."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.workers.codec import WorkerRecordCodec
from punt_vox.voxd.workers.hooks import WorkerHooks
from punt_vox.voxd.workers.steering import WorkerSteering

if TYPE_CHECKING:
    from punt_vox.voxd.hook_context_store import HookContextStore
    from punt_vox.voxd.voice_call.progress import ProgressEvents
    from punt_vox.voxd.workers.registry import WorkerRegistry
    from punt_vox.voxd.workers.steering_types import DecisionChoice, SteerReceipt
    from punt_vox.voxd.workers.types import WorkerPayload


@final
class WorkerActor:
    """Serialize mutations while running blocking provider I/O off the event loop."""

    __slots__ = ("_lock", "_registry", "_steering")
    _registry: WorkerRegistry
    _lock: asyncio.Lock
    _steering: WorkerSteering

    def __new__(cls, registry: WorkerRegistry) -> Self:
        """Bind one registry and its serialization lock."""
        self = super().__new__(cls)
        self._registry = registry
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
        """Start one persistent worker in an allowlisted project."""
        async with self._lock:
            record = await asyncio.to_thread(
                self._registry.start, task, project, provider=provider, harness=harness
            )
        return WorkerRecordCodec.encode(record)

    async def stop(self, worker_id: str) -> WorkerPayload:
        """Stop one worker; its worktree and branch are retained."""
        async with self._lock:
            record = await asyncio.to_thread(self._registry.stop, worker_id)
        return WorkerRecordCodec.encode(record)

    async def workers(self) -> list[WorkerPayload]:
        """Reconcile and return every known worker."""
        async with self._lock:
            records = await asyncio.to_thread(self._registry.list)
        return [WorkerRecordCodec.encode(record) for record in records]

    async def steer(
        self,
        worker_id: str,
        words: str,
        *,
        urgent: bool,
        choice: DecisionChoice | None = None,
    ) -> SteerReceipt:
        """Deliver words verbatim and report whether the worker heard them."""
        return await self.steer_request(
            {"worker_id": worker_id, "words": words, "urgent": urgent, "choice": choice}
        )

    async def steer_request(self, msg: dict[str, object]) -> SteerReceipt:
        """Validate and deliver one wire steering request."""
        return await self._steering.handle(msg)

    def hooks(
        self, store: HookContextStore, events: ProgressEvents | None = None
    ) -> WorkerHooks:
        """Return hook ingestion that checkpoints under this actor's lock."""
        return WorkerHooks(store, self._registry, self._lock, events)
