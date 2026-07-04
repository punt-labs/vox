"""Experience-level tests for MusicLoop.

These drive the REAL loop with a fake subprocess that records each argv and
lets the test control when each player ends. The bas7 gap was proving rotation
by calling the scheduler directly while the loop looped one file; here every
assertion is on what the loop actually spawned -- a *different* file on
track-end, no generation at 12, the current player surviving a vibe change.
"""
# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

from music.conftest import FakeTrackStore
from punt_vox.voxd.music.filler import PoolFiller
from punt_vox.voxd.music.generator import TrackGenerator
from punt_vox.voxd.music.loop import MusicLoop
from punt_vox.voxd.music.scheduler import MusicScheduler

if TYPE_CHECKING:
    import pytest

__all__: list[str] = []

_PROVIDER = "punt_vox.providers.elevenlabs_music.ElevenLabsMusicProvider.generate_track"
_SUBPROCESS = "punt_vox.voxd.music.loop.asyncio.create_subprocess_exec"
_CHOICE = "punt_vox.voxd.music.pool.secrets.choice"


def _first_candidate(seq: Sequence[Path]) -> Path:
    """Deterministic stand-in for secrets.choice: pick the first candidate."""
    return seq[0]


class _FakeProc:
    """A fake player subprocess whose end the test controls."""

    def __init__(self) -> None:
        self.returncode: int | None = None
        self.stderr = None
        self._ended = asyncio.Event()

    async def wait(self) -> int:
        await self._ended.wait()
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def kill(self) -> None:
        self.returncode = -9
        self._ended.set()

    def end(self) -> None:
        """Signal a natural end (rc 0)."""
        self.returncode = 0
        self._ended.set()


class _FakePlayers:
    """Record spawned player commands and hand back controllable procs."""

    def __init__(self) -> None:
        self.commands: list[list[str]] = []
        self.procs: list[_FakeProc] = []
        self._spawned = asyncio.Event()

    async def spawn(self, *cmd: str, **_kwargs: Any) -> _FakeProc:
        self.commands.append(list(cmd))
        proc = _FakeProc()
        self.procs.append(proc)
        self._spawned.set()
        return proc

    async def wait_for(self, count: int) -> None:
        """Block until at least ``count`` players have been spawned."""
        while len(self.commands) < count:
            self._spawned.clear()
            if len(self.commands) >= count:
                return
            await asyncio.wait_for(self._spawned.wait(), timeout=2.0)

    def track_of(self, index: int) -> str:
        """Return the basename of the track the ``index``-th spawn played."""
        return Path(self.commands[index][-1]).name

    def end(self, index: int) -> None:
        """End the ``index``-th player naturally."""
        self.procs[index].end()


def _scheduler(store: FakeTrackStore) -> MusicScheduler:
    return MusicScheduler(TrackGenerator(store))


def _seed_pool(store: FakeTrackStore, vibe: str, style: str, count: int) -> str:
    """Register ``count`` tracks for one pool; return the prefix."""
    prefix = TrackGenerator.pool_prefix((vibe, style))
    for i in range(count):
        store.add(f"{prefix}{i:02d}")
    return prefix


async def _settle() -> None:
    """Yield to the loop a few times so it can process a control signal."""
    for _ in range(10):
        await asyncio.sleep(0)


async def _stop(task: asyncio.Task[None]) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


class TestAutoAdvance:
    """Track-end advances to a DIFFERENT track with no skip and no generation."""

    def test_track_end_advances_to_different_track(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(_CHOICE, _first_candidate)
        store = FakeTrackStore()
        _seed_pool(store, "calm", "jazz", 12)  # full -> no fill
        sched = _scheduler(store)
        players = _FakePlayers()

        async def _drive() -> None:
            with (
                patch(_SUBPROCESS, players.spawn),
                patch(_PROVIDER, AsyncMock()) as gen_track,
            ):
                await sched.turn_on("u1", "jazz", ("calm", ""), "")
                task = asyncio.create_task(MusicLoop(sched).run())
                await players.wait_for(1)
                first = players.track_of(0)
                players.end(0)  # natural end, no skip_next call
                await players.wait_for(2)
                second = players.track_of(1)
                await _stop(task)
                assert second != first  # auto-advanced to a different file
                gen_track.assert_not_called()  # advance never generates

        asyncio.run(_drive())


class TestRotateAtTwelve:
    """A full pool rotates across advances with zero provider calls."""

    def test_full_pool_rotates_without_generation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(_CHOICE, _first_candidate)
        store = FakeTrackStore()
        _seed_pool(store, "calm", "jazz", 12)
        sched = _scheduler(store)
        players = _FakePlayers()

        async def _drive() -> None:
            with (
                patch(_SUBPROCESS, players.spawn),
                patch(_PROVIDER, AsyncMock()) as gen_track,
            ):
                await sched.turn_on("u1", "jazz", ("calm", ""), "")
                task = asyncio.create_task(MusicLoop(sched).run())
                await players.wait_for(1)
                players.end(0)
                await players.wait_for(2)
                players.end(1)
                await players.wait_for(3)
                await _stop(task)
                gen_track.assert_not_called()
                # Consecutive tracks never repeat the just-played one.
                assert players.track_of(0) != players.track_of(1)
                assert players.track_of(1) != players.track_of(2)

        asyncio.run(_drive())


class TestVibeChangeFinishesThenSwitches:
    """A vibe change does not interrupt; the next track comes from the new pool."""

    def test_current_survives_then_switches_pool(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(_CHOICE, _first_candidate)
        store = FakeTrackStore()
        _seed_pool(store, "calm", "jazz", 12)  # pool A
        _seed_pool(store, "bright", "jazz", 12)  # pool B (style stays jazz)
        sched = _scheduler(store)
        players = _FakePlayers()

        async def _drive() -> None:
            with (
                patch(_SUBPROCESS, players.spawn),
                patch(_PROVIDER, AsyncMock()),
            ):
                await sched.turn_on("u1", "jazz", ("calm", ""), "")
                task = asyncio.create_task(MusicLoop(sched).run())
                await players.wait_for(1)
                assert players.track_of(0).startswith("calm_jazz_")

                sched.update_vibe("u1", ("bright", ""))
                await _settle()
                # Finish-current-first: the playing proc is NOT killed.
                assert players.procs[0].returncode is None

                players.end(0)  # current song finishes
                await players.wait_for(2)
                await _stop(task)
                assert players.procs[0].returncode == 0  # ended, not killed (-9)
                assert players.track_of(1).startswith("bright_jazz_")

        asyncio.run(_drive())


class TestOffCancelsFill:
    """/music off cancels the background fill and stops playback."""

    def test_off_cancels_fill_no_orphan(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_CHOICE, _first_candidate)
        store = FakeTrackStore()
        prefix = _seed_pool(store, "calm", "jazz", 1)  # partial -> fill runs
        sched = _scheduler(store)
        players = _FakePlayers()
        gen_started = asyncio.Event()
        gen_calls = 0

        async def blocking_generate(
            self: TrackGenerator, vibe: tuple[str, str], style: str, name: str
        ) -> tuple[Any, str]:
            nonlocal gen_calls
            gen_calls += 1
            gen_started.set()
            await asyncio.Event().wait()  # blocks until the task is cancelled
            return store.path_for("never"), "never"

        async def _drive() -> None:
            with (
                patch(_SUBPROCESS, players.spawn),
                patch.object(TrackGenerator, "generate", blocking_generate),
            ):
                await sched.turn_on("u1", "jazz", ("calm", ""), "")
                task = asyncio.create_task(MusicLoop(sched).run())
                await players.wait_for(1)  # #00 playing
                await asyncio.wait_for(gen_started.wait(), timeout=2.0)
                assert sched.filling  # a fill task is live

                await sched.turn_off()
                await _settle()
                assert not sched.filling  # fill cancelled synchronously
                assert players.procs[0].returncode == -9  # player killed
                assert len(store.tracks_for(prefix)) == 1  # no orphaned track
                await _stop(task)

        asyncio.run(_drive())
        assert gen_calls == 1


class TestSingleTrackLoopsThenAdvances:
    """The sole-track transient loops #1 until #2 lands, then advances."""

    def test_loops_then_advances_when_second_lands(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(_CHOICE, _first_candidate)
        store = FakeTrackStore()
        prefix = _seed_pool(store, "calm", "jazz", 1)  # only #00
        sched = _scheduler(store)
        players = _FakePlayers()
        release = asyncio.Event()

        async def gated_generate(
            self: TrackGenerator, vibe: tuple[str, str], style: str, name: str
        ) -> tuple[Any, str]:
            await release.wait()
            release.clear()  # one track per release
            n = len(store.tracks_for(prefix))
            stem = f"{prefix}{n:02d}"
            return store.add(stem), stem

        async def _drive() -> None:
            with (
                patch(_SUBPROCESS, players.spawn),
                patch.object(TrackGenerator, "generate", gated_generate),
                patch.object(PoolFiller, "_backoff", AsyncMock()),
            ):
                await sched.turn_on("u1", "jazz", ("calm", ""), "")
                task = asyncio.create_task(MusicLoop(sched).run())
                await players.wait_for(1)  # plays #00
                players.end(0)
                await players.wait_for(2)  # still only #00 -> loops #00
                assert players.track_of(1) == players.track_of(0)

                release.set()  # let #01 land
                await _settle()
                players.end(1)
                await players.wait_for(3)  # now advances to #01
                await _stop(task)
                assert players.track_of(2) != players.track_of(0)
                assert players.track_of(2) == f"{prefix}01.mp3"

        asyncio.run(_drive())


class TestGeneratingFirstThenPlays:
    """An empty pool waits for track #1, then plays it (generating-first)."""

    def test_empty_pool_awaits_first_track(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(_CHOICE, _first_candidate)
        store = FakeTrackStore()
        prefix = TrackGenerator.pool_prefix(("calm", "jazz"))
        sched = _scheduler(store)
        players = _FakePlayers()
        release = asyncio.Event()

        async def gated_generate(
            self: TrackGenerator, vibe: tuple[str, str], style: str, name: str
        ) -> tuple[Any, str]:
            await release.wait()
            release.clear()
            n = len(store.tracks_for(prefix))
            stem = f"{prefix}{n:02d}"
            return store.add(stem), stem

        async def _drive() -> None:
            with (
                patch(_SUBPROCESS, players.spawn),
                patch.object(TrackGenerator, "generate", gated_generate),
                patch.object(PoolFiller, "_backoff", AsyncMock()),
            ):
                await sched.turn_on("u1", "jazz", ("calm", ""), "")
                task = asyncio.create_task(MusicLoop(sched).run())
                await _settle()
                assert not players.commands  # nothing plays before #1 exists
                assert sched.state == "generating"

                release.set()  # #00 lands
                await players.wait_for(1)
                await _stop(task)
                assert players.track_of(0) == f"{prefix}00.mp3"

        asyncio.run(_drive())


class TestSkipEmptyPoolIsNoOp:
    """/music next while generating-first is a no-op (Z finding #1)."""

    def test_skip_before_first_track_does_not_spawn_or_crash(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(_CHOICE, _first_candidate)
        store = FakeTrackStore()
        prefix = TrackGenerator.pool_prefix(("calm", "jazz"))
        sched = _scheduler(store)
        players = _FakePlayers()
        release = asyncio.Event()

        async def gated_generate(
            self: TrackGenerator, vibe: tuple[str, str], style: str, name: str
        ) -> tuple[Any, str]:
            await release.wait()
            release.clear()
            n = len(store.tracks_for(prefix))
            stem = f"{prefix}{n:02d}"
            return store.add(stem), stem

        async def _drive() -> None:
            with (
                patch(_SUBPROCESS, players.spawn),
                patch.object(TrackGenerator, "generate", gated_generate),
                patch.object(PoolFiller, "_backoff", AsyncMock()),
            ):
                await sched.turn_on("u1", "jazz", ("calm", ""), "")
                task = asyncio.create_task(MusicLoop(sched).run())
                await _settle()

                result = sched.skip_next("u1")  # pool still empty
                assert result.status == "ignored"
                await _settle()
                assert not players.commands  # no player spawned by the skip

                release.set()  # #1 still lands normally afterward
                await players.wait_for(1)
                await _stop(task)

        asyncio.run(_drive())


class TestTurnOnWhileActiveDoesNotCrash:
    """turn_on while music is already active must never crash the loop.

    Regression for the "play"-without-a-pending-track crash: a non-replay
    turn_on used to signal "play", which drove the loop to call
    take_pending_track() on an empty queue and raise RuntimeError, killing the
    background MusicLoop task. A retarget must finish-current and enter the new
    pool instead, and the loop task must stay alive and keep advancing.
    """

    def test_turn_on_while_playing_survives_and_switches(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(_CHOICE, _first_candidate)
        store = FakeTrackStore()
        _seed_pool(store, "calm", "jazz", 12)  # pool A, full -> no fill
        _seed_pool(store, "bright", "jazz", 12)  # pool B (style stays jazz)
        sched = _scheduler(store)
        players = _FakePlayers()

        async def _drive() -> None:
            with (
                patch(_SUBPROCESS, players.spawn),
                patch(_PROVIDER, AsyncMock()),
            ):
                await sched.turn_on("u1", "jazz", ("calm", ""), "")
                task = asyncio.create_task(MusicLoop(sched).run())
                await players.wait_for(1)
                assert players.track_of(0).startswith("calm_jazz_")

                # turn_on a NEW pool while the first track is still playing.
                await sched.turn_on("u1", "jazz", ("bright", ""), "")
                await _settle()
                assert not task.done()  # the loop task survived (no crash)
                assert players.procs[0].returncode is None  # current not killed

                players.end(0)  # current song finishes -> enter the new pool
                await players.wait_for(2)
                await _stop(task)
                assert players.track_of(1).startswith("bright_jazz_")

        asyncio.run(_drive())

    def test_turn_on_while_generating_first_survives(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(_CHOICE, _first_candidate)
        store = FakeTrackStore()
        prefix = TrackGenerator.pool_prefix(("calm", "jazz"))
        sched = _scheduler(store)
        players = _FakePlayers()
        release = asyncio.Event()

        async def gated_generate(
            self: TrackGenerator, vibe: tuple[str, str], style: str, name: str
        ) -> tuple[Any, str]:
            await release.wait()
            release.clear()
            n = len(store.tracks_for(prefix))
            stem = f"{prefix}{n:02d}"
            return store.add(stem), stem

        async def _drive() -> None:
            with (
                patch(_SUBPROCESS, players.spawn),
                patch.object(TrackGenerator, "generate", gated_generate),
                patch.object(PoolFiller, "_backoff", AsyncMock()),
            ):
                await sched.turn_on("u1", "jazz", ("calm", ""), "")  # empty pool
                task = asyncio.create_task(MusicLoop(sched).run())
                await _settle()
                assert sched.state == "generating"

                # turn_on again while awaiting the first track: must not crash.
                await sched.turn_on("u1", "jazz", ("calm", ""), "")
                await _settle()
                assert not task.done()  # still alive, still generating-first

                release.set()  # first track lands -> playback begins
                await players.wait_for(1)
                await _stop(task)
                assert players.track_of(0) == f"{prefix}00.mp3"

        asyncio.run(_drive())


class TestRestartResumesFromDisk:
    """Restart (turn_on with tracks on disk) plays now and resumes fill."""

    def test_partial_pool_plays_now_and_fills(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(_CHOICE, _first_candidate)
        store = FakeTrackStore()
        prefix = _seed_pool(store, "calm", "jazz", 5)  # 5 on disk
        sched = _scheduler(store)
        players = _FakePlayers()
        release = asyncio.Event()
        gen_calls = 0

        async def gated_generate(
            self: TrackGenerator, vibe: tuple[str, str], style: str, name: str
        ) -> tuple[Any, str]:
            nonlocal gen_calls
            gen_calls += 1
            await release.wait()
            release.clear()
            n = len(store.tracks_for(prefix))
            stem = f"{prefix}{n:02d}"
            return store.add(stem), stem

        async def _drive() -> None:
            with (
                patch(_SUBPROCESS, players.spawn),
                patch.object(TrackGenerator, "generate", gated_generate),
                patch.object(PoolFiller, "_backoff", AsyncMock()),
            ):
                await sched.turn_on("u1", "jazz", ("calm", ""), "")
                task = asyncio.create_task(MusicLoop(sched).run())
                await players.wait_for(1)  # plays immediately from disk
                assert sched.filling  # fill resumed
                await _stop(task)

        asyncio.run(_drive())
        assert gen_calls >= 1  # fill resumed from the on-disk count
