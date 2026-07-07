"""Tests for :class:`InterruptRace` -- the interrupt-vs-natural-end decision.

These port the unit-level ``_player_errored`` assertions that lived on
``ProgramLoop`` before the race machinery was extracted, and add the three
outcomes ``interrupted`` decides between: a user interrupt wins outright, a clean
player exit is a natural end (not interrupted), and a *raised* ``wait`` is a
player error retrieved and logged here so it never masquerades as a clean advance.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.programs.interrupt_race import InterruptRace

if TYPE_CHECKING:
    import pytest


@final
class _CleanProcess:
    """A process that exits cleanly the moment its end is signalled (test control)."""

    __slots__ = ("_ended", "rc")
    rc: int | None
    _ended: asyncio.Event

    def __new__(cls) -> Self:
        self = super().__new__(cls)
        self.rc = None
        self._ended = asyncio.Event()
        return self

    async def wait(self) -> int:
        await self._ended.wait()
        return self.rc if self.rc is not None else 0

    async def kill(self) -> None:
        self.rc = -9
        self._ended.set()

    def end(self, rc: int = 0) -> None:
        self.rc = rc
        self._ended.set()


@final
class _RaisingProcess:
    """A process whose ``wait`` raises -- a player error, not a clean end."""

    __slots__ = ()

    async def wait(self) -> int:
        msg = "transport gone"
        raise RuntimeError(msg)

    async def kill(self) -> None:
        return None


class TestPlayerErrored:
    """A settled ``wait`` that raised is a player error; a clean one is not."""

    async def test_flags_a_raised_wait(self, caplog: pytest.LogCaptureFixture) -> None:
        async def _boom() -> int:
            msg = "transport gone"
            raise RuntimeError(msg)

        task: asyncio.Task[int] = asyncio.ensure_future(_boom())
        with contextlib.suppress(RuntimeError):
            await task
        with caplog.at_level(logging.ERROR):
            errored = InterruptRace._player_errored(task)
        assert errored is True
        assert any("player wait failed" in r.getMessage() for r in caplog.records)

    async def test_passes_a_clean_wait(self) -> None:
        async def _clean() -> int:
            return 0

        task: asyncio.Task[int] = asyncio.ensure_future(_clean())
        await task
        assert InterruptRace._player_errored(task) is False


class TestInterrupted:
    """The three outcomes ``interrupted`` decides between."""

    async def test_user_interrupt_wins_over_a_running_player(self) -> None:
        interrupt = asyncio.Event()
        interrupt.set()  # a skip / off / play-a-part already posted
        race = InterruptRace(interrupt)
        proc = _CleanProcess()  # still playing -- wait() would block

        assert await race.interrupted(proc) is True

    async def test_clean_end_is_not_interrupted(self) -> None:
        interrupt = asyncio.Event()
        race = InterruptRace(interrupt)
        proc = _CleanProcess()
        proc.end(0)  # a natural end, no interrupt pending

        assert await race.interrupted(proc) is False

    async def test_raised_wait_counts_as_interrupted_and_logs(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        interrupt = asyncio.Event()
        race = InterruptRace(interrupt)
        with caplog.at_level(logging.ERROR):
            interrupted = await race.interrupted(_RaisingProcess())
        assert interrupted is True  # a player error is not a clean advance
        assert any("player wait failed" in r.getMessage() for r in caplog.records)
