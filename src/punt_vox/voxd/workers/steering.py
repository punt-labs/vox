"""Bounded input acknowledgement without monopolizing the worker actor."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Self, final
from weakref import WeakValueDictionary

from punt_vox.voxd._parse import parse_optional_int, parse_required_str
from punt_vox.voxd.workers.registry import WorkerRegistry

if TYPE_CHECKING:
    from punt_vox.voxd.workers.steering_types import DecisionChoice, SteerReceipt


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

    async def handle(self, msg: dict[str, object]) -> SteerReceipt:
        """Acknowledge heard input, never idle; interrupt need not emit Stop."""
        worker_id = parse_required_str(msg, "worker_id")
        words = parse_required_str(msg, "words")
        urgent = msg.get("urgent", False)
        if not isinstance(urgent, bool):
            raise ValueError("urgent must be a bool")
        choice = msg.get("choice")
        if choice is not None and choice not in ("approve", "deny"):
            raise ValueError("choice must be approve or deny")
        deadline = asyncio.get_running_loop().time() + self.TIMEOUT
        worker_lock = self._workers.setdefault(worker_id, asyncio.Lock())
        await self._acquire(worker_lock, deadline)
        try:
            return await self._deliver(
                worker_id,
                words,
                urgent=urgent,
                deadline=deadline,
                choice=choice,
                prompt_epoch=parse_optional_int(msg, "prompt_epoch"),
            )
        finally:
            worker_lock.release()

    @staticmethod
    async def _acquire(lock: asyncio.Lock, deadline: float) -> None:
        remaining = deadline - asyncio.get_running_loop().time()
        try:
            await asyncio.wait_for(lock.acquire(), timeout=remaining)
        except TimeoutError as exc:
            raise ValueError(
                "worker is busy with another steer or control operation"
            ) from exc

    async def _deliver(
        self,
        worker_id: str,
        words: str,
        *,
        urgent: bool,
        deadline: float,
        choice: DecisionChoice | None,  # None submits ordinary follow-up text.
        prompt_epoch: int | None,  # None binds direct control to the live prompt.
    ) -> SteerReceipt:
        await self._acquire(self._lock, deadline)
        try:
            if asyncio.get_running_loop().time() >= deadline:
                raise ValueError("steering deadline expired before delivery")
            baseline = await asyncio.to_thread(
                self._registry.steer,
                worker_id,
                words,
                urgent=urgent,
                choice=choice,
                prompt_epoch=prompt_epoch,
            )
        finally:
            self._lock.release()
        receipt = await self._receipt(worker_id, words, baseline)
        return {
            "worker_id": worker_id,
            "heard": receipt is not None,
            "receipt_seq": receipt,
            "urgent": urgent,
            "choice": choice,
        }

    # PY-TS-14: timeout without a matching input has no receipt sequence.
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
