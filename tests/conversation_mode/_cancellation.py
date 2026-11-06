"""Cancel a task at the moment it is waiting on a child task it just cancelled."""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable

__all__ = ["cancelled_while_reaping", "swallow_a_cancellation"]


async def cancelled_while_reaping(work: Awaitable[object]) -> asyncio.Task[object]:
    """Run *work*; cancel it once it has cancelled its one child and awaits it."""
    runner: asyncio.Task[object] = asyncio.ensure_future(work)
    bystanders = {runner, asyncio.current_task()}
    while not (children := asyncio.all_tasks() - bystanders):
        await asyncio.sleep(0)
    (child,) = children
    while not child.cancelling():
        await asyncio.sleep(0)
    runner.cancel()
    return runner


async def swallow_a_cancellation() -> None:
    """Be cancelled once and swallow it, leaving ``cancelling()`` raised."""
    current = asyncio.current_task()
    assert current is not None
    asyncio.get_running_loop().call_soon(current.cancel)
    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.sleep(10)
