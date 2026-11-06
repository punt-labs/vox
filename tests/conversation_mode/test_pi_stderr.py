"""Tests for :class:`PiStderr`: pi's stderr is always drained, never fatal."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import pytest

from punt_vox.voxd.conversation_mode import pi_stderr as stderr_module
from punt_vox.voxd.conversation_mode.pi_stderr import PiStderr

if TYPE_CHECKING:
    from collections.abc import Awaitable


async def _drained(*lines: bytes, limit: int = 2**16) -> PiStderr:
    stream = asyncio.StreamReader(limit=limit)
    stderr = PiStderr(stream)
    for line in lines:
        stream.feed_data(line)
    stream.feed_eof()
    await stderr.stop()
    return stderr


async def test_the_tail_keeps_the_last_lines_joined() -> None:
    stderr = await _drained(b"first\n", b"second\n")
    assert stderr.tail() == "first | second"


async def test_an_empty_stderr_says_so() -> None:
    assert (await _drained()).tail() == "(no stderr)"


async def test_no_pipe_drains_nothing() -> None:
    stderr = PiStderr(None)
    await stderr.stop()
    assert stderr.tail() == "(no stderr)"


async def test_the_tail_is_bounded() -> None:
    stderr = await _drained(*(f"line {i}\n".encode() for i in range(100)))
    assert stderr.tail().startswith("line 80 |")


async def test_an_over_long_line_is_skipped_and_the_drain_goes_on(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger=stderr_module.__name__):
        stderr = await _drained(b"x" * 64 + b"\n", b"real reason\n", limit=16)
    assert (stderr.tail(), "over-long" in caplog.text) == ("real reason", True)


async def test_stop_ends_a_drain_whose_stream_never_closes() -> None:
    stderr = PiStderr(asyncio.StreamReader())
    await stderr.stop()
    assert stderr.tail() == "(no stderr)"


async def _cancelled_while_stopping(stop: Awaitable[None]) -> asyncio.Task[None]:
    """Cancel the task running *stop* once it is waiting on the cancelled drain."""
    stopper = asyncio.ensure_future(stop)
    others = asyncio.all_tasks() - {stopper, asyncio.current_task()}
    (drain,) = others
    while not drain.cancelling():
        await asyncio.sleep(0)
    stopper.cancel()
    return stopper


async def test_stop_never_swallows_its_caller_s_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(stderr_module, "_SETTLE_S", 0.0)
    stderr = PiStderr(asyncio.StreamReader())
    stopper = await _cancelled_while_stopping(stderr.stop())
    with pytest.raises(asyncio.CancelledError):
        await stopper
