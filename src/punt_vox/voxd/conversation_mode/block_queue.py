"""A bounded queue of captured PCM blocks that keeps the newest when full."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import Self, final

__all__ = ["BlockQueue"]

logger = logging.getLogger(__name__)


@final
class BlockQueue:
    """Hold at most ``capacity`` blocks; a full queue drops its oldest block.

    The producer is an audio callback that never waits, so a reader that
    falls behind (or has not started) must lose audio rather than let the
    backlog grow without bound and reach the agent late. Drops are counted
    and reported once, at the next read, so a slow reader leaves one log line
    rather than one per block.
    """

    __slots__ = ("_blocks", "_dropped", "_ready")
    _blocks: deque[bytes]
    _ready: asyncio.Event
    _dropped: int

    def __new__(cls, capacity: int) -> Self:
        """Create an empty queue with room for ``capacity`` blocks.

        Raises:
            ValueError: When ``capacity`` is not positive.
        """
        if capacity <= 0:
            raise ValueError(f"capacity must be positive, not {capacity}")
        self = super().__new__(cls)
        self._blocks = deque(maxlen=capacity)
        self._ready = asyncio.Event()
        self._dropped = 0
        return self

    def __len__(self) -> int:
        return len(self._blocks)

    def put(self, pcm: bytes) -> None:
        """Append a block, evicting the oldest when the queue is full."""
        if len(self._blocks) == self._blocks.maxlen:
            self._dropped += 1
        self._blocks.append(pcm)
        self._ready.set()

    async def get(self) -> bytes:
        """Return the oldest held block, waiting for one when empty."""
        while not self._blocks:
            self._ready.clear()
            await self._ready.wait()
        if self._dropped:
            logger.warning(
                "mic capture: dropped %d blocks the reader had not taken",
                self._dropped,
            )
            self._dropped = 0
        return self._blocks.popleft()

    def discard(self) -> None:
        """Forget every held block and any drops: the reader starts fresh."""
        self._blocks.clear()
        self._dropped = 0
        self._ready.clear()
