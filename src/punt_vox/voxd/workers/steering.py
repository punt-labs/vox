"""Bounded input acknowledgement without monopolizing the worker actor."""

from __future__ import annotations

import asyncio
from typing import Self, final

from punt_vox.voxd._parse import parse_required_str
from punt_vox.voxd.workers.registry import WorkerRegistry


@final
class WorkerSteering:
    """Serialize delivery, but release the actor while awaiting an input receipt."""

    __slots__ = ("_lock", "_registry")
    _registry: WorkerRegistry
    _lock: asyncio.Lock
    TIMEOUT = 10.0
    INTERVAL = 0.1

    def __new__(cls, registry: WorkerRegistry, lock: asyncio.Lock) -> Self:
        """Bind the registry's shared serialization lock."""
        self = super().__new__(cls)
        self._registry, self._lock = registry, lock
        return self

    async def handle(self, msg: dict[str, object]) -> dict[str, object]:
        """Acknowledge heard input, never idle; interrupt need not emit Stop."""
        worker_id = parse_required_str(msg, "worker_id")
        words = parse_required_str(msg, "words")
        urgent = msg.get("urgent", False)
        if not isinstance(urgent, bool):
            raise ValueError("urgent must be a bool")
        async with self._lock:
            baseline = await asyncio.to_thread(
                self._registry.steer, worker_id, words, urgent=urgent
            )
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
        return {
            "worker_id": worker_id,
            "heard": receipt is not None,
            "receipt_seq": receipt,
            "urgent": urgent,
        }
