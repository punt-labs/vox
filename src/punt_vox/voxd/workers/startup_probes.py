"""Run independent startup probes under one wall-clock budget."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, wait
from contextvars import ContextVar, copy_context
from typing import TYPE_CHECKING, final

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

_DEADLINE = ContextVar("worker_startup_deadline", default=float("inf"))
_BUDGET_S = 1.8
_COMMAND_S = 0.4


@final
class StartupProbes:
    """Bound tmux startup without letting timed-out probes retire records."""

    __slots__ = ()

    @staticmethod
    def run[T](operation: Callable[[], T]) -> T:
        """Set the deadline for one startup, including concurrent child probes."""
        token = _DEADLINE.set(time.monotonic() + _BUDGET_S)
        try:
            return operation()
        finally:
            _DEADLINE.reset(token)

    @staticmethod
    def timeout(normal: float) -> float:
        """Keep normal command budgets; shorten them within startup recovery."""
        deadline = _DEADLINE.get()
        if deadline == float("inf"):
            return normal
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Worker startup probe budget expired")
        return min(remaining, _COMMAND_S, normal)

    @staticmethod
    def completed[T, R](items: Iterable[T], operation: Callable[[T], R]) -> list[R]:
        """Return only results that settled before the common deadline."""
        deadline = _DEADLINE.get()
        if deadline == float("inf"):
            return [operation(item) for item in items]
        executor = ThreadPoolExecutor(max_workers=16, thread_name_prefix="vox-probe")
        try:
            jobs = [
                executor.submit(copy_context().run, operation, item) for item in items
            ]
            done, _ = wait(jobs, timeout=deadline - time.monotonic())
            return [job.result() for job in jobs if job in done]
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
