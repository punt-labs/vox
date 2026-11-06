"""Tests for :class:`PiStderr`: pi's stderr is always drained, never fatal."""

from __future__ import annotations

import asyncio
import logging

import pytest

from conversation_mode._cancellation import (
    cancelled_while_reaping,
    swallow_a_cancellation,
)
from punt_vox.voxd.conversation_mode import pi_stderr as stderr_module
from punt_vox.voxd.conversation_mode.pi_stderr import PiStderr


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


async def test_stop_never_swallows_its_caller_s_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(stderr_module, "_SETTLE_S", 0.0)
    stderr = PiStderr(asyncio.StreamReader())
    stopper = await cancelled_while_reaping(stderr.stop())
    with pytest.raises(asyncio.CancelledError):
        await stopper


async def test_stop_after_a_swallowed_cancellation_raises_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An old, already-handled cancel must not turn orderly cleanup into one."""
    monkeypatch.setattr(stderr_module, "_SETTLE_S", 0.0)

    async def stop_after_a_handled_cancel() -> str:
        await swallow_a_cancellation()
        await PiStderr(asyncio.StreamReader()).stop()
        return "stopped"

    stopping = asyncio.ensure_future(stop_after_a_handled_cancel())
    assert await stopping == "stopped"
