"""Tests for MusicScheduler -- domain methods, selection, and fill control.

Domain tests inject the in-memory FakeTrackStore (Amendment A): no tmp_path,
no filesystem. The selection decision, the empty-pool skip guard, and the fill
lifecycle (retarget on vibe, cancel on off) are all exercised directly.
"""
# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from music.conftest import FakeTrackStore
from punt_vox.voxd.music.generator import TrackGenerator
from punt_vox.voxd.music.pool import POOL_SIZE
from punt_vox.voxd.music.scheduler import MusicScheduler
from punt_vox.voxd.music.types import MusicResponse

__all__: list[str] = []

_CHOICE = "punt_vox.voxd.music.pool.secrets.choice"


def _scheduler(store: FakeTrackStore | None = None) -> MusicScheduler:
    """Build a scheduler over an in-memory store."""
    return MusicScheduler(TrackGenerator(store or FakeTrackStore()))


def _seed(store: FakeTrackStore, vibe: str, style: str, count: int) -> str:
    """Register ``count`` tracks for one pool; return the prefix."""
    prefix = TrackGenerator.pool_prefix((vibe, style))
    for i in range(count):
        store.add(f"{prefix}{i:02d}")
    return prefix


def _first(seq: Sequence[Path]) -> Path:
    """Deterministic stand-in for secrets.choice: pick the first candidate."""
    return seq[0]


def _tuned(sched: MusicScheduler, vibe: str, style: str) -> MusicScheduler:
    """Put a scheduler in the 'on' state for one (vibe, style)."""
    sched._channel.activate()
    sched._channel.claim("u1")
    sched._playlist.retune((vibe, ""), style)
    return sched


class TestTurnOn:
    """turn_on adopts a pool, signals the loop, and starts the fill."""

    def test_turn_on_empty_pool_acks_generating(self) -> None:
        sched = _scheduler()
        with patch.object(TrackGenerator, "generate", AsyncMock()):
            result = asyncio.run(
                sched.turn_on("u1", "techno", ("focused", "[calm]"), "")
            )
        assert result == MusicResponse(status="generating")
        assert sched.mode == "on"
        assert sched.owner == "u1"
        assert sched.style == "techno"
        assert sched.vibe == ("focused", "[calm]")
        assert sched.changed.is_set()

    def test_turn_on_partial_pool_acks_playing(self) -> None:
        store = FakeTrackStore()
        _seed(store, "focused", "techno", 3)
        sched = _scheduler(store)
        with patch.object(TrackGenerator, "generate", AsyncMock()):
            result = asyncio.run(sched.turn_on("u1", "techno", ("focused", ""), ""))
        assert result.status == "playing"  # a member is on disk to play now

    def test_turn_on_rejects_empty_owner(self) -> None:
        sched = _scheduler()
        with pytest.raises(ValueError, match="owner_id is required"):
            asyncio.run(sched.turn_on("", "techno", ("focused", ""), ""))

    def test_turn_on_replay_existing_track(self) -> None:
        store = FakeTrackStore()
        track = store.add("my_focus")
        sched = _scheduler(store)
        with patch.object(TrackGenerator, "generate", AsyncMock()):

            async def _run() -> MusicResponse:
                result = await sched.turn_on(
                    "u1", "jazz", ("happy", "[warm]"), "my focus"
                )
                await sched.shutdown()  # cancel the resumed fill task cleanly
                return result

            result = asyncio.run(_run())
        assert result.status == "playing"
        assert result.track == str(track)
        assert result.name == "my_focus"
        assert sched.mode == "on"
        assert sched.has_pending_track  # queued for the loop to replay

    def test_turn_on_replay_resumes_fill(self) -> None:
        # Finding A: a named replay onto a pool with < 12 tracks must restart
        # the background fill so the pool keeps growing toward POOL_SIZE.
        store = FakeTrackStore()
        _seed(store, "happy", "jazz", 3)  # < 12 -> fill should resume
        store.add("happy_jazz_replayme")
        sched = _scheduler(store)
        with patch.object(TrackGenerator, "generate", AsyncMock()):

            async def _run() -> None:
                await sched.turn_on("u1", "jazz", ("happy", ""), "happy_jazz_replayme")
                await asyncio.sleep(0)  # let the resumed fill task start
                assert sched.filling  # fill active for the replayed track's pool
                await sched.shutdown()

            asyncio.run(_run())

    def test_turn_on_invalid_track_name(self) -> None:
        sched = _scheduler()
        with pytest.raises(ValueError, match="invalid track name"):
            asyncio.run(sched.turn_on("u1", "", ("", ""), "---"))


class TestKeyPreflight:
    """turn_on refuses to start generation without a usable provider key."""

    def test_missing_key_reports_clear_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
        sched = _scheduler()
        with pytest.raises(ValueError, match="requires an ElevenLabs API key"):
            asyncio.run(sched.turn_on("u1", "techno", ("focused", ""), ""))
        assert sched.mode == "off"  # never entered the generating state
        assert not sched.filling  # no silent attempt-then-disable

    def test_blank_key_reports_clear_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ELEVENLABS_API_KEY", "   ")  # whitespace-only is empty
        sched = _scheduler()
        with pytest.raises(ValueError, match="requires an ElevenLabs API key"):
            asyncio.run(sched.turn_on("u1", "techno", ("focused", ""), ""))
        assert sched.mode == "off"

    def test_present_key_starts_normally(self) -> None:
        # The autouse fixture supplies a key; the happy path acks generating.
        sched = _scheduler()
        with patch.object(TrackGenerator, "generate", AsyncMock()):
            result = asyncio.run(sched.turn_on("u1", "techno", ("focused", ""), ""))
        assert result == MusicResponse(status="generating")

    def test_present_key_with_transient_error_is_not_a_key_error(self) -> None:
        # A present key that later hits quota/rate limits must NOT be
        # misreported as a missing key: turn_on passes the preflight and
        # returns "generating"; the transient failure is handled downstream
        # by the fill's own retry/backoff (vox-ig52), off the turn_on path.
        sched = _scheduler()
        quota = AsyncMock(side_effect=RuntimeError("quota_exceeded"))
        with patch.object(TrackGenerator, "generate", quota):

            async def _run() -> MusicResponse:
                result = await sched.turn_on("u1", "techno", ("focused", ""), "")
                await sched.shutdown()  # cancel the retrying fill cleanly
                return result

            result = asyncio.run(_run())
        assert result == MusicResponse(status="generating")


class TestTurnOff:
    """turn_off cancels the fill synchronously and stops playback."""

    def test_turn_off_resets_state(self) -> None:
        store = FakeTrackStore()
        _seed(store, "calm", "jazz", 3)
        sched = _tuned(_scheduler(store), "calm", "jazz")
        result = asyncio.run(sched.turn_off())
        assert result == MusicResponse(status="stopped")
        assert sched.mode == "off"
        assert sched.state == "idle"
        assert not sched.filling
        assert sched.track is None  # PY-EN-5: avoid-repeat key cleared

    def test_turn_off_kills_proc(self) -> None:
        sched = _scheduler()
        proc = MagicMock()
        proc.returncode = None
        proc.kill = MagicMock()
        proc.wait = AsyncMock(return_value=0)
        sched._proc = proc
        asyncio.run(sched.turn_off())
        proc.kill.assert_called_once()
        assert sched.proc is None

    def test_turn_off_clears_owner(self) -> None:
        # Finding #5: a stopped session must release ownership so a stale
        # forwarded vibe from the old owner is not accepted after the stop.
        store = FakeTrackStore()
        _seed(store, "calm", "jazz", 3)
        sched = _tuned(_scheduler(store), "calm", "jazz")
        asyncio.run(sched.turn_off())
        assert sched.owner == ""


class TestPlayTrack:
    """play_track queues a named replay and switches to that track's pool."""

    def test_play_track_queues_named(self) -> None:
        store = FakeTrackStore()
        track = store.add("chill_vibes")
        sched = _scheduler(store)
        with patch.object(TrackGenerator, "generate", AsyncMock()):

            async def _run() -> MusicResponse:
                result = await sched.play_track("chill vibes", "u1")
                await sched.shutdown()  # cancel the resumed fill task cleanly
                return result

            result = asyncio.run(_run())
        assert result.status == "playing"
        assert result.track == str(track)
        assert result.name == "chill_vibes"
        assert sched.mode == "on"
        assert sched.has_pending_track
        assert sched.changed.is_set()

    def test_play_track_not_found(self) -> None:
        sched = _scheduler()
        with pytest.raises(ValueError, match="track not found"):
            asyncio.run(sched.play_track("nope", "u1"))

    def test_play_track_empty_name(self) -> None:
        sched = _scheduler()
        with pytest.raises(ValueError, match="name is required"):
            asyncio.run(sched.play_track("", "u1"))

    def test_play_track_empty_owner(self) -> None:
        sched = _scheduler()
        with pytest.raises(ValueError, match="owner_id is required"):
            asyncio.run(sched.play_track("x", ""))


class TestUpdateVibe:
    """update_vibe retargets the fill and signals a pending switch."""

    def test_update_vibe_matching_owner_retargets_fill(self) -> None:
        store = FakeTrackStore()
        sched = _tuned(_scheduler(store), "old", "jazz")
        with patch.object(TrackGenerator, "generate", AsyncMock()):

            async def _run() -> MusicResponse:
                result = sched.update_vibe("u1", ("happy", "[warm]"))
                await asyncio.sleep(0)  # let the retargeted fill task start
                return result

            result = asyncio.run(_run())
        assert result.status == "generating"  # new pool empty -> will fill
        assert sched.vibe == ("happy", "[warm]")
        assert sched.changed.is_set()

    def test_update_vibe_into_full_pool_acks_playing(self) -> None:
        store = FakeTrackStore()
        _seed(store, "calm", "jazz", POOL_SIZE)
        sched = _tuned(_scheduler(store), "restless", "jazz")
        result = sched.update_vibe("u1", ("calm", ""))
        assert result.status == "playing"  # full pool -> rotate, no fill

    def test_update_vibe_non_owner_ignored(self) -> None:
        sched = _tuned(_scheduler(), "old", "jazz")
        result = sched.update_vibe("other", ("happy", "[warm]"))
        assert result == MusicResponse(status="ignored")

    def test_update_vibe_when_off_is_ignored(self) -> None:
        # Finding #4: a forwarded vibe while music is off must not retarget the
        # fill -- that would spend credits generating a pool nobody is playing.
        sched = _scheduler()
        sched._channel.claim("u1")  # owner set, but mode stays "off"
        result = sched.update_vibe("u1", ("happy", "[warm]"))
        assert result == MusicResponse(status="ignored")
        assert not sched.filling  # no background generation started while off

    def test_update_vibe_same_vibe_ignored(self) -> None:
        sched = _tuned(_scheduler(), "happy", "jazz")
        sched._playlist.retune(("happy", "[warm]"), "jazz")
        result = sched.update_vibe("u1", ("happy", "[warm]"))
        assert result == MusicResponse(status="ignored")
        assert not sched.changed.is_set()

    def test_update_vibe_empty_owner(self) -> None:
        sched = _scheduler()
        with pytest.raises(ValueError, match="owner_id is required"):
            sched.update_vibe("", ("happy", "[warm]"))


class TestSkipNext:
    """skip_next signals an advance, but is a no-op on an empty pool."""

    def test_skip_next_with_pool_signals(self) -> None:
        store = FakeTrackStore()
        _seed(store, "calm", "jazz", 3)
        sched = _tuned(_scheduler(store), "calm", "jazz")
        result = sched.skip_next("u1")
        assert result == MusicResponse(status="playing")
        assert sched.changed.is_set()

    def test_skip_next_empty_pool_is_noop(self) -> None:
        # Z finding #1: generating-first -> skip must not pick from an empty pool.
        sched = _tuned(_scheduler(), "calm", "jazz")  # no tracks on disk
        result = sched.skip_next("u1")
        assert result == MusicResponse(status="ignored")
        assert not sched.changed.is_set()

    def test_skip_next_during_custom_name_play_signals(self) -> None:
        # Finding #2: a custom-named track is playing but its stem does not match
        # the (vibe, style) prefix, so is_empty is True; skip must still advance
        # -- the guard keys off the playing track, not the session glob.
        sched = _tuned(_scheduler(), "focused", "techno")  # no matching-prefix files
        sched.mark_playing(Path("/fake/tracks/my_custom.mp3"))  # custom track playing
        result = sched.skip_next("u1")
        assert result == MusicResponse(status="playing")
        assert sched.changed.is_set()

    def test_skip_next_when_off_ignored(self) -> None:
        sched = _scheduler()
        sched._channel.deactivate()
        assert sched.skip_next("u1") == MusicResponse(status="ignored")

    def test_skip_next_empty_owner(self) -> None:
        sched = _scheduler()
        with pytest.raises(ValueError, match="owner_id is required"):
            sched.skip_next("")


class TestSelection:
    """select_next_track is the pure advance/rotate decision over the pool."""

    def test_select_avoids_the_just_played_track(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(_CHOICE, _first)
        store = FakeTrackStore()
        prefix = _seed(store, "calm", "jazz", POOL_SIZE)
        sched = _tuned(_scheduler(store), "calm", "jazz")
        current = store.path_for(f"{prefix}00")
        sched.mark_playing(current)
        chosen = sched.select_next_track()
        assert chosen != current  # never the just-played track

    def test_select_single_track_pool_loops(self) -> None:
        store = FakeTrackStore()
        prefix = _seed(store, "calm", "jazz", 1)
        sched = _tuned(_scheduler(store), "calm", "jazz")
        only = store.path_for(f"{prefix}00")
        sched.mark_playing(only)
        assert sched.select_next_track() == only  # the sole-track transient

    def test_rotation_never_repeats_previous(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(_CHOICE, _first)
        store = FakeTrackStore()
        _seed(store, "calm", "jazz", POOL_SIZE)
        sched = _tuned(_scheduler(store), "calm", "jazz")
        previous: Path | None = None
        for _ in range(20):
            chosen = sched.select_next_track()
            sched.mark_playing(chosen)
            assert chosen != previous
            previous = chosen


class TestControlChannel:
    """take_control returns then clears the pending action."""

    def test_take_control_is_one_shot(self) -> None:
        store = FakeTrackStore()
        _seed(store, "calm", "jazz", 3)
        sched = _tuned(_scheduler(store), "calm", "jazz")
        sched.skip_next("u1")
        assert sched.take_control() == "skip"
        assert sched.take_control() == "none"  # reset after read


class TestDisable:
    """disable stops music and clears all ownership and transient state."""

    def test_disable_clears_ownership_pending_and_avoid_key(self) -> None:
        # Finding #6: an unrecoverable failure must leave nothing a later
        # message could act on -- owner, queued replay, and avoid-repeat key.
        store = FakeTrackStore()
        prefix = _seed(store, "calm", "jazz", 2)
        sched = _tuned(_scheduler(store), "calm", "jazz")
        sched._pending_track = store.path_for(f"{prefix}00")
        sched.mark_playing(store.path_for(f"{prefix}01"))
        sched.disable()
        assert sched.mode == "off"
        assert sched.owner == ""  # ownership released
        assert not sched.has_pending_track  # queued replay dropped
        assert sched.track is None  # avoid-repeat key cleared (PY-EN-5)


class TestConstructionDefaults:
    """A fresh scheduler starts idle (migrated from test_voxd_music)."""

    def test_defaults(self) -> None:
        sched = _scheduler()
        assert sched.mode == "off"
        assert sched.style == ""
        assert sched.owner == ""
        assert sched.vibe == ("", "")
        assert sched.track is None
        assert sched.proc is None
        assert sched.state == "idle"
        assert not sched.changed.is_set()
        assert not sched.filling

    def test_surviving_property_round_trips(self) -> None:
        # Replaces test_field_round_trips: the read properties reflect their
        # backing state. _replay and _track_name are gone under the new model;
        # style/vibe/track now delegate to the Playlist.
        sched = _scheduler()
        sched._channel.activate()
        assert sched.mode == "on"
        sched._channel.claim("sess-1")
        assert sched.owner == "sess-1"
        sched._state = "playing"
        assert sched.state == "playing"
        sched._playlist.retune(("chill", "[mellow]"), "jazz")
        assert sched.vibe == ("chill", "[mellow]")
        assert sched.style == "jazz"
        played = Path("/fake/tracks/chill_jazz_00.mp3")
        sched._playlist.mark_playing(played)
        assert sched.track == played


class TestKillProc:
    """kill_proc safely terminates the music subprocess (migrated)."""

    def test_kills_running_proc(self) -> None:
        sched = _scheduler()
        proc = MagicMock()
        proc.returncode = None
        proc.kill = MagicMock()
        proc.wait = AsyncMock(return_value=0)
        sched._proc = proc
        asyncio.run(sched.kill_proc())
        proc.kill.assert_called_once()
        assert sched.proc is None

    def test_noop_when_no_proc(self) -> None:
        sched = _scheduler()
        asyncio.run(sched.kill_proc())
        assert sched.proc is None

    def test_noop_when_proc_already_exited(self) -> None:
        sched = _scheduler()
        proc = MagicMock()
        proc.returncode = 0
        proc.kill = MagicMock()
        sched._proc = proc
        asyncio.run(sched.kill_proc())
        proc.kill.assert_not_called()
        assert sched.proc is None


class TestWaitActive:
    """wait_active returns without blocking when music is already on (migrated).

    Guards the lost-wakeup race: mode set to 'on' before the loop reaches
    wait_active must not leave it blocked on changed.wait().
    """

    def test_returns_immediately_when_already_on(self) -> None:
        sched = _scheduler()
        sched._channel.activate()

        async def _run() -> None:
            await asyncio.wait_for(sched.wait_active(), timeout=1.0)

        asyncio.run(_run())  # must not time out

    def test_wakes_when_turned_on(self) -> None:
        sched = _scheduler()

        async def _run() -> None:
            waiter = asyncio.create_task(sched.wait_active())
            await asyncio.sleep(0)
            sched._channel.activate()
            sched.changed.set()
            await asyncio.wait_for(waiter, timeout=1.0)

        asyncio.run(_run())
