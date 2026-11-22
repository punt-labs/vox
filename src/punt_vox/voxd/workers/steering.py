"""Bounded input acknowledgement without monopolizing the worker actor."""

from __future__ import annotations

import asyncio
from typing import Self, final
from weakref import WeakValueDictionary

from punt_vox.voxd._parse import parse_required_str
from punt_vox.voxd.workers.registry import WorkerRegistry


@final
class WorkerSteering:
    """Serialize delivery, but release the actor while awaiting an input receipt."""

    __slots__ = ("_lock", "_registry", "_workers")
    _registry: WorkerRegistry
    _lock: asyncio.Lock
    _workers: WeakValueDictionary[str, asyncio.Lock]
    TIMEOUT = 10.0
    INTERVAL = 0.1

    def __new__(cls, registry: WorkerRegistry, lock: asyncio.Lock) -> Self:
        """Bind the registry's shared serialization lock."""
        self = super().__new__(cls)
        self._registry, self._lock = registry, lock
        self._workers = WeakValueDictionary()
        return self

    async def handle(self, msg: dict[str, object]) -> dict[str, object]:
        """Acknowledge heard input, never idle; interrupt need not emit Stop."""
        worker_id = parse_required_str(msg, "worker_id")
        words = parse_required_str(msg, "words")
        urgent = msg.get("urgent", False)
        if not isinstance(urgent, bool):
            raise ValueError("urgent must be a bool")
        worker_lock = self._workers.setdefault(worker_id, asyncio.Lock())
        try:
            await asyncio.wait_for(worker_lock.acquire(), timeout=self.TIMEOUT)
        except TimeoutError as exc:
            raise ValueError("worker is busy with another steer") from exc
        try:
            return await self._deliver(worker_id, words, urgent=urgent)
        finally:
            worker_lock.release()

    async def _deliver(
        self, worker_id: str, words: str, *, urgent: bool
    ) -> dict[str, object]:
        async with self._lock:
            baseline = await asyncio.to_thread(
                self._registry.steer, worker_id, words, urgent=urgent
            )
        receipt = await self._receipt(worker_id, words, baseline)
        return {
            "worker_id": worker_id,
            "heard": receipt is not None,
            "receipt_seq": receipt,
            "urgent": urgent,
        }

    async def _receipt(self, worker_id: str, words: str, baseline: int) -> int | None:
        receipt = None
        try:
            async with asyncio.timeout(self.TIMEOUT):
                while receipt is None:
                    async with self._lock:
                        receipt = self._registry.receipt(worker_id, words, baseline)
                    if receipt is None:
                        await asyncio.sleep(self.INTERVAL)
        except TimeoutError:
            pass
        return receipt
