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
from punt_vox.music_prompts import PromptSet
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
            self: TrackGenerator,
            vibe: tuple[str, str],
            style: str,
            name: str,
            prompts: PromptSet,
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
            self: TrackGenerator,
            vibe: tuple[str, str],
            style: str,
            name: str,
            prompts: PromptSet,
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
            self: TrackGenerator,
            vibe: tuple[str, str],
            style: str,
            name: str,
            prompts: PromptSet,
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


class TestVibeToNonEmptyPoolDuringGeneratingFirst:
    """A vibe change to a pool with tracks on disk plays now, never hangs.

    Regression for the generating-first hang: ``_await_first`` unconditionally
    re-raced ``await_first_track()`` on a vibe retarget. If the new pool is
    already full, no fill runs, so that wait never completes and the loop hangs.
    The loop must play an on-disk track immediately when the retargeted pool is
    non-empty.
    """

    def test_retarget_to_full_pool_plays_without_awaiting_generation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(_CHOICE, _first_candidate)
        store = FakeTrackStore()
        _seed_pool(store, "bright", "jazz", 12)  # pool B full (style stays jazz)
        sched = _scheduler(store)
        players = _FakePlayers()
        never = asyncio.Event()  # pool A's first generation never lands

        async def gated_generate(
            self: TrackGenerator,
            vibe: tuple[str, str],
            style: str,
            name: str,
            prompts: PromptSet,
        ) -> tuple[Any, str]:
            await never.wait()  # empty pool A stays generating-first forever
            msg = "unreachable"
            raise AssertionError(msg)

        async def _drive() -> None:
            with (
                patch(_SUBPROCESS, players.spawn),
                patch.object(TrackGenerator, "generate", gated_generate),
                patch.object(PoolFiller, "_backoff", AsyncMock()),
            ):
                await sched.turn_on("u1", "jazz", ("calm", ""), "")  # empty pool A
                task = asyncio.create_task(MusicLoop(sched).run())
                await _settle()
                assert sched.state == "generating"  # awaiting A's first track
                assert not players.commands

                sched.update_vibe("u1", ("bright", ""))  # retarget to FULL pool B
                await players.wait_for(1)  # plays from disk at once -- no hang
                await _stop(task)
                assert players.track_of(0).startswith("bright_jazz_")

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
            self: TrackGenerator,
            vibe: tuple[str, str],
            style: str,
            name: str,
            prompts: PromptSet,
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


class TestSkipDuringGeneratingFirstWithDiskTrack:
    """/music next during generating-first advances from disk once a track lands.

    Regression for the dropped-skip hang: ``_await_first`` handled off / play /
    vibe but not skip. A skip issued while awaiting the first track (after a
    track has landed on disk, so ``skip_next`` signals rather than no-oping) was
    silently ignored -- the loop kept re-racing ``await_first_track()``, which a
    disk-only track never resolves, so it hung. The loop must play the on-disk
    track at once, mirroring the vibe-to-non-empty-pool path.
    """

    def test_skip_after_disk_track_lands_plays_without_awaiting_generation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(_CHOICE, _first_candidate)
        store = FakeTrackStore()
        prefix = TrackGenerator.pool_prefix(("calm", "jazz"))
        sched = _scheduler(store)
        players = _FakePlayers()
        never = asyncio.Event()  # the fill's first generation never lands

        async def gated_generate(
            self: TrackGenerator,
            vibe: tuple[str, str],
            style: str,
            name: str,
            prompts: PromptSet,
        ) -> tuple[Any, str]:
            await never.wait()  # await_first_track stays blocked forever
            msg = "unreachable"
            raise AssertionError(msg)

        async def _drive() -> None:
            with (
                patch(_SUBPROCESS, players.spawn),
                patch.object(TrackGenerator, "generate", gated_generate),
                patch.object(PoolFiller, "_backoff", AsyncMock()),
            ):
                await sched.turn_on("u1", "jazz", ("calm", ""), "")  # empty pool
                task = asyncio.create_task(MusicLoop(sched).run())
                await _settle()
                assert sched.state == "generating"  # awaiting the first track
                assert not players.commands

                # A track lands on disk (out of band from the blocked fill), so
                # the pool is no longer empty and skip_next signals rather than
                # no-oping. Before the fix the loop dropped the skip and hung.
                store.add(f"{prefix}00")
                result = sched.skip_next("u1")
                assert result.status == "playing"
                await players.wait_for(1)  # plays from disk at once -- no hang
                await _stop(task)
                assert players.track_of(0) == f"{prefix}00.mp3"

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
            self: TrackGenerator,
            vibe: tuple[str, str],
            style: str,
            name: str,
            prompts: PromptSet,
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


class TestNamedReplayResumesFill:
    """Finding A: a named replay onto a < 12 pool resumes the background fill.

    Before the fix, play_track/_replay_named cancelled the fill and never
    restarted it, so after a named replay the pool stopped growing toward 12.
    A replay must retarget selection AND restart the fill for the track's pool,
    cancelling the old fill before starting the new one (single-fill invariant).
    """

    def test_replay_restarts_fill_toward_twelve(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(_CHOICE, _first_candidate)
        store = FakeTrackStore()
        prefix = _seed_pool(store, "calm", "jazz", 3)  # < 12 on disk
        sched = _scheduler(store)
        players = _FakePlayers()
        release = asyncio.Event()
        gen_calls = 0

        async def gated_generate(
            self: TrackGenerator,
            vibe: tuple[str, str],
            style: str,
            name: str,
            prompts: PromptSet,
        ) -> tuple[Any, str]:
            nonlocal gen_calls
            gen_calls += 1
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
                await players.wait_for(1)  # plays a disk member; fill running
                assert sched.filling
                old_fill = sched._playlist._filler._task

                # Replay a named member of the same pool.
                await sched.play_track(f"{prefix}00", "u1")
                await _settle()
                new_fill = sched._playlist._filler._task
                assert new_fill is not None
                assert old_fill is not None
                assert old_fill is not new_fill  # a fresh fill for the replay
                assert old_fill.done()  # the old fill was cancelled
                assert not new_fill.done()  # exactly one live fill task
                assert sched.filling

                # The resumed fill actually generates toward 12 after the replay.
                calls_before = gen_calls
                release.set()  # release the in-flight (old) generation
                await _settle()
                release.set()  # let the resumed fill make one more
                await _settle()
                assert gen_calls > calls_before  # generate called after replay

                await _stop(task)

        asyncio.run(_drive())


class TestNamedFirstNonMatchingStemDoesNotCrash:
    """Finding B: track-end over an empty pool loops the current track, no crash.

    A named-first track (``/music on --name X`` for a not-yet-saved X) is named
    ``X`` -- a stem that does NOT match the (vibe, style) pool prefix. If the
    fill then exhausts its retries before landing any pool member, the pool for
    that prefix stays empty while ``X`` is playing. Before the fix, track-end
    called ``pick_next`` on the empty pool and raised ``ValueError``, crashing
    the loop task. The loop must replay the current track instead.
    """

    def test_track_end_replays_current_when_pool_has_no_member(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(_CHOICE, _first_candidate)
        store = FakeTrackStore()
        sched = _scheduler(store)
        players = _FakePlayers()

        async def gen_first_then_fail(
            self: TrackGenerator,
            vibe: tuple[str, str],
            style: str,
            name: str,
            prompts: PromptSet,
        ) -> tuple[Any, str]:
            if name == "mysong":  # the named-first track (stem misses the prefix)
                return store.add(name), name
            msg = "no pool member"  # every auto-named generation fails
            raise RuntimeError(msg)

        async def _drive() -> None:
            with (
                patch(_SUBPROCESS, players.spawn),
                patch.object(TrackGenerator, "generate", gen_first_then_fail),
                patch.object(PoolFiller, "_backoff", AsyncMock()),
            ):
                await sched.turn_on("u1", "jazz", ("calm", ""), "mysong")
                task = asyncio.create_task(MusicLoop(sched).run())
                await players.wait_for(1)  # plays the named-first track
                assert players.track_of(0) == "mysong.mp3"

                players.end(0)  # track ends -> advance over an empty pool
                await players.wait_for(2)  # replays current instead of crashing
                assert not task.done()  # loop survived the empty-pool advance
                await _stop(task)
                assert players.track_of(1) == "mysong.mp3"  # looped the current

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
            self: TrackGenerator,
            vibe: tuple[str, str],
            style: str,
            name: str,
            prompts: PromptSet,
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
