"""Stop a background task, absorbing only the cancellation the stop itself caused."""

from __future__ import annotations

import asyncio
from typing import final

__all__ = ["TaskReaper"]


@final
class TaskReaper:
    """Cancel a child task and wait for it to end.

    Awaiting a child just cancelled raises ``CancelledError`` -- the child's
    own, which the reaper absorbs. The task doing the reaping may be
    cancelled during that wait too, and that cancellation is its caller's:
    it is always raised. The two are told apart by counting the reaping
    task's pending cancellations before and after the wait, never by
    whether any are pending -- an earlier cancellation its owner already
    handled leaves ``cancelling()`` raised for good.
    """

    __slots__ = ()

    @classmethod
    async def reap[T](cls, child: asyncio.Task[T]) -> None:
        """Cancel *child* and wait for it; its own failure propagates."""
        before = cls._pending()
        child.cancel()
        try:
            await child
        except asyncio.CancelledError:
            if cls._pending() > before or not child.cancelled():
                raise

    @staticmethod
    def _pending() -> int:
        """Return how many cancellations of the running task are pending."""
        task = asyncio.current_task()
        return task.cancelling() if task is not None else 0
