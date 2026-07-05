"""The ``Sleeper`` seam -- injectable async backoff so tests never really wait.

The fill loop backs off between transient-error retries. Production injects
:class:`RealSleeper` (a thin ``asyncio.sleep`` wrapper); tests inject a no-op
sleeper so the ``retrying -> recover`` path runs in microseconds.
"""

from __future__ import annotations

import asyncio
from typing import Protocol, final

__all__ = ["RealSleeper", "Sleeper"]


class Sleeper(Protocol):
    """Awaitable backoff (single-method strategy, PY-DP-11)."""

    async def sleep(self, seconds: float) -> None:
        """Sleep for ``seconds`` (a no-op in tests)."""
        ...


@final
class RealSleeper:
    """Back off with the real event-loop clock (production)."""

    __slots__ = ()

    async def sleep(self, seconds: float) -> None:
        """Sleep for ``seconds`` on the event loop."""
        await asyncio.sleep(seconds)
