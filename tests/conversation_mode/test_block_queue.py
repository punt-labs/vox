"""Tests for :mod:`punt_vox.voxd.conversation_mode.block_queue`."""

from __future__ import annotations

import asyncio
import logging

import pytest

from punt_vox.voxd.conversation_mode.block_queue import BlockQueue

_LOGGER = "punt_vox.voxd.conversation_mode.block_queue"


def _blocks(count: int) -> list[bytes]:
    return [str(index).encode() for index in range(count)]


async def test_a_full_queue_keeps_the_newest_blocks_and_logs_the_drop_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """100 blocks before the first read: the reader sees block 50 onward."""
    queue = BlockQueue(50)
    for pcm in _blocks(100):
        queue.put(pcm)
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        first = await queue.get()
        rest = [await queue.get() for _ in range(49)]
    assert first == b"50"
    assert rest[-1] == b"99"
    assert len(queue) == 0
    lines = [r.message for r in caplog.records if r.name == _LOGGER]
    assert lines == ["mic capture: dropped 50 blocks the reader had not taken"]


async def test_get_waits_for_a_block() -> None:
    queue = BlockQueue(4)
    reader = asyncio.ensure_future(queue.get())
    await asyncio.sleep(0)
    assert not reader.done()
    queue.put(b"\x01\x00")
    assert await reader == b"\x01\x00"


async def test_discard_empties_the_queue_silently(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Blocks captured before the reader was ready are not stale input."""
    queue = BlockQueue(2)
    for pcm in _blocks(5):
        queue.put(pcm)
    queue.discard()
    assert len(queue) == 0
    queue.put(b"fresh")
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        assert await queue.get() == b"fresh"
    assert [r for r in caplog.records if r.name == _LOGGER] == []


async def test_a_queue_without_room_is_refused() -> None:
    with pytest.raises(ValueError, match="capacity must be positive"):
        BlockQueue(0)
