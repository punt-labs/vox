"""Tests for punt_vox.voxd.music_scheduler -- MusicScheduler extraction."""
# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from punt_vox.voxd.music.generator import TrackGenerator
from punt_vox.voxd.music.loop import MusicLoop
from punt_vox.voxd.music.scheduler import MusicScheduler


def _make_scheduler(tmp_path: Path | None = None) -> MusicScheduler:
    """Build a MusicScheduler with a TrackGenerator writing to tmp_path."""
    if tmp_path is not None:
        gen = TrackGenerator(tmp_path)
        return MusicScheduler(gen)
    gen = TrackGenerator(Path("/tmp/vox-test-music"))
    return MusicScheduler(gen)


class TestMusicSchedulerFields:
    """MusicScheduler must expose all music fields."""

    def test_music_mode_default(self) -> None:
        scheduler = _make_scheduler()
        assert scheduler.mode == "off"

    def test_music_style_default(self) -> None:
        scheduler = _make_scheduler()
        assert scheduler.style == ""

    def test_music_owner_default(self) -> None:
        scheduler = _make_scheduler()
        assert scheduler.owner == ""

    def test_music_vibe_default(self) -> None:
        scheduler = _make_scheduler()
        assert scheduler.vibe == ("", "")

    def test_music_track_default(self) -> None:
        scheduler = _make_scheduler()
        assert scheduler.track is None

    def test_music_proc_default(self) -> None:
        scheduler = _make_scheduler()
        assert scheduler.proc is None

    def test_music_state_default(self) -> None:
        scheduler = _make_scheduler()
        assert scheduler.state == "idle"

    def test_music_changed_default(self) -> None:
        scheduler = _make_scheduler()
        assert isinstance(scheduler.changed, asyncio.Event)
        assert not scheduler.changed.is_set()

    def test_field_round_trips(self) -> None:
        """Setting via private attributes reads back correctly."""
        scheduler = _make_scheduler()
        scheduler._mode = "on"
        assert scheduler.mode == "on"
        scheduler._style = "jazz"
        assert scheduler.style == "jazz"
        scheduler._owner = "sess-1"
        assert scheduler.owner == "sess-1"
        scheduler._vibe = ("chill", "[mellow]")
        assert scheduler.vibe == ("chill", "[mellow]")
        scheduler._track_name = "my-track"
        assert scheduler.track_name == "my-track"
        scheduler._replay = True
        assert scheduler.replay is True
        scheduler._state = "playing"
        assert scheduler.state == "playing"


class TestMusicLoopStateTransitions:
    """MusicScheduler.loop: generation, playback, vibe changes, crash recovery."""

    def test_generates_and_plays_then_stops_on_off(self, tmp_path: Path) -> None:
        """Full cycle: mode on -> generate -> play -> mode off."""
        scheduler = _make_scheduler(tmp_path)

        call_log: list[str] = []

        async def fake_generate_track(
            self: object, prompt: str, duration_ms: int, output_path: Path
        ) -> Path:
            call_log.append("generate")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"fake-music-data")
            return output_path

        async def fake_subprocess(*args: object, **kwargs: object) -> MagicMock:
            call_log.append("play")
            proc = MagicMock()
            proc.returncode = 0

            async def _wait() -> int:
                await asyncio.sleep(0.01)
                return 0

            proc.wait = _wait
            proc.kill = MagicMock()
            return proc

        async def _drive() -> None:
            with (
                patch(
                    "punt_vox.providers.elevenlabs_music.ElevenLabsMusicProvider"
                    ".generate_track",
                    fake_generate_track,
                ),
                patch(
                    "punt_vox.voxd.music.loop.asyncio.create_subprocess_exec",
                    fake_subprocess,
                ),
            ):
                task = asyncio.create_task(MusicLoop(scheduler).run())
                await asyncio.sleep(0)

                # Turn music on.
                scheduler._mode = "on"
                scheduler._owner = "test-session"
                scheduler._vibe = ("focused", "[calm]")
                scheduler.changed.set()
                await asyncio.sleep(0.05)

                # Turn music off.
                scheduler._mode = "off"
                scheduler.changed.set()
                await asyncio.sleep(0.05)

                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        asyncio.run(_drive())

        assert "generate" in call_log
        assert "play" in call_log

    def test_crash_recovery_retries_with_backoff(self, tmp_path: Path) -> None:
        """Three failures in a row disable music mode."""
        scheduler = _make_scheduler(tmp_path)
        scheduler._mode = "on"
        scheduler._owner = "test-session"
        scheduler._vibe = ("focused", "")
        scheduler.changed.set()

        attempt_count = 0

        async def failing_generate(
            self: object, prompt: str, duration_ms: int, output_path: Path
        ) -> Path:
            nonlocal attempt_count
            attempt_count += 1
            msg = f"generation failed (attempt {attempt_count})"
            raise RuntimeError(msg)

        async def _drive() -> None:
            nonlocal attempt_count
            with (
                patch(
                    "punt_vox.providers.elevenlabs_music.ElevenLabsMusicProvider"
                    ".generate_track",
                    failing_generate,
                ),
                patch.object(
                    MusicLoop,
                    "_backoff_sleep",
                    new=AsyncMock(),
                ),
            ):
                task = asyncio.create_task(MusicLoop(scheduler).run())
                # Yield control so the loop can run its 3 retries.
                for _ in range(20):
                    await asyncio.sleep(0)
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        asyncio.run(_drive())

        assert attempt_count == 3
        assert scheduler.mode == "off"
        assert scheduler.state == "idle"

    def test_vibe_change_during_generation_triggers_regeneration(
        self, tmp_path: Path
    ) -> None:
        """Setting music_changed during generation causes a new track."""
        scheduler = _make_scheduler(tmp_path)
        scheduler._mode = "on"
        scheduler._owner = "test-session"
        scheduler._vibe = ("focused", "")
        scheduler.changed.set()

        generation_count = 0

        async def counting_generate(
            self: object, prompt: str, duration_ms: int, output_path: Path
        ) -> Path:
            nonlocal generation_count
            generation_count += 1
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"fake-music-data")

            # On first generation, simulate a vibe change mid-flight.
            if generation_count == 1:
                scheduler._vibe = ("happy", "[warm]")
                scheduler.changed.set()

            return output_path

        async def fake_subprocess(*args: object, **kwargs: object) -> MagicMock:
            proc = MagicMock()
            proc.returncode = 0

            async def _wait() -> int:
                await asyncio.sleep(0.01)
                return 0

            proc.wait = _wait
            proc.kill = MagicMock()
            return proc

        async def _drive() -> None:
            with (
                patch(
                    "punt_vox.providers.elevenlabs_music.ElevenLabsMusicProvider"
                    ".generate_track",
                    counting_generate,
                ),
                patch(
                    "punt_vox.voxd.music.loop.asyncio.create_subprocess_exec",
                    fake_subprocess,
                ),
            ):
                task = asyncio.create_task(MusicLoop(scheduler).run())
                await asyncio.sleep(0.15)

                scheduler._mode = "off"
                scheduler.changed.set()
                await asyncio.sleep(0.05)

                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        asyncio.run(_drive())

        assert generation_count >= 2


class TestMusicLoopGaplessHandoff:
    """Old track must keep looping while generation runs concurrently."""

    def test_old_track_loops_during_generation(self, tmp_path: Path) -> None:
        """Playback subprocess stays alive the entire time generation runs."""
        scheduler = _make_scheduler(tmp_path)
        scheduler._mode = "on"
        scheduler._owner = "test-session"
        scheduler._vibe = ("focused", "[calm]")
        scheduler.changed.set()

        generation_count = 0
        play_count = 0
        procs_alive_during_gen: list[bool] = []

        async def slow_generate(
            self: object, prompt: str, duration_ms: int, output_path: Path
        ) -> Path:
            nonlocal generation_count
            generation_count += 1
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"fake-music-data")

            if generation_count == 2:
                proc = scheduler.proc
                is_alive = proc is not None and proc.returncode is None
                procs_alive_during_gen.append(is_alive)
                await asyncio.sleep(0.1)
                proc = scheduler.proc
                is_alive = proc is not None and proc.returncode is None
                procs_alive_during_gen.append(is_alive)

            return output_path

        async def fake_subprocess(*args: object, **kwargs: object) -> MagicMock:
            nonlocal play_count
            play_count += 1
            proc = MagicMock()
            proc.returncode = None

            async def _wait() -> int:
                if proc.returncode is not None:
                    return int(proc.returncode)
                await asyncio.sleep(5.0)
                proc.returncode = 0
                return 0

            proc.wait = _wait
            proc.kill = MagicMock(side_effect=lambda: setattr(proc, "returncode", -9))
            return proc

        async def _drive() -> None:
            with (
                patch(
                    "punt_vox.providers.elevenlabs_music.ElevenLabsMusicProvider"
                    ".generate_track",
                    slow_generate,
                ),
                patch(
                    "punt_vox.voxd.music.loop.asyncio.create_subprocess_exec",
                    fake_subprocess,
                ),
            ):
                task = asyncio.create_task(MusicLoop(scheduler).run())
                await asyncio.sleep(0.05)

                scheduler._vibe = ("happy", "[warm]")
                scheduler.changed.set()

                await asyncio.sleep(0.3)

                scheduler._mode = "off"
                scheduler.changed.set()
                await asyncio.sleep(0.05)
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        asyncio.run(_drive())

        assert generation_count >= 2, (
            f"expected >=2 generations, got {generation_count}"
        )
        assert play_count >= 2, f"expected >=2 playback spawns, got {play_count}"
        assert all(procs_alive_during_gen), (
            f"old track was killed during generation: {procs_alive_during_gen}"
        )

    def test_second_vibe_change_cancels_inflight_generation(
        self, tmp_path: Path
    ) -> None:
        """A second vibe change during generation cancels the first and starts fresh."""
        scheduler = _make_scheduler(tmp_path)
        scheduler._mode = "on"
        scheduler._owner = "test-session"
        scheduler._vibe = ("focused", "[calm]")
        scheduler.changed.set()

        generation_vibes: list[str] = []
        gen_event = asyncio.Event()

        async def tracking_generate(
            self: object, prompt: str, duration_ms: int, output_path: Path
        ) -> Path:
            vibe, _ = scheduler.vibe
            generation_vibes.append(vibe)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"fake-music-data")

            if len(generation_vibes) == 2:
                gen_event.set()
                await asyncio.sleep(0.5)
            elif len(generation_vibes) == 3:
                pass

            return output_path

        async def fake_subprocess(*args: object, **kwargs: object) -> MagicMock:
            proc = MagicMock()
            proc.returncode = None

            async def _wait() -> int:
                if proc.returncode is not None:
                    return int(proc.returncode)
                await asyncio.sleep(5.0)
                proc.returncode = 0
                return 0

            proc.wait = _wait
            proc.kill = MagicMock(side_effect=lambda: setattr(proc, "returncode", -9))
            return proc

        async def _drive() -> None:
            with (
                patch(
                    "punt_vox.providers.elevenlabs_music.ElevenLabsMusicProvider"
                    ".generate_track",
                    tracking_generate,
                ),
                patch(
                    "punt_vox.voxd.music.loop.asyncio.create_subprocess_exec",
                    fake_subprocess,
                ),
            ):
                task = asyncio.create_task(MusicLoop(scheduler).run())
                await asyncio.sleep(0.05)

                scheduler._vibe = ("happy", "[warm]")
                scheduler.changed.set()
                await asyncio.wait_for(gen_event.wait(), timeout=1.0)

                scheduler._vibe = ("energetic", "[upbeat]")
                scheduler.changed.set()
                await asyncio.sleep(0.3)

                scheduler._mode = "off"
                scheduler.changed.set()
                await asyncio.sleep(0.05)
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        asyncio.run(_drive())

        assert len(generation_vibes) >= 3, (
            f"expected >=3 generation attempts, got "
            f"{len(generation_vibes)}: {generation_vibes}"
        )
        assert generation_vibes[-1] == "energetic"


class TestKillMusicProc:
    """MusicScheduler.kill_proc safely terminates the music subprocess."""

    def test_kills_running_proc(self) -> None:
        scheduler = _make_scheduler()
        proc = MagicMock()
        proc.returncode = None
        proc.kill = MagicMock()
        proc.wait = AsyncMock(return_value=0)
        scheduler._proc = proc

        asyncio.run(scheduler.kill_proc())

        proc.kill.assert_called_once()
        assert scheduler.proc is None

    def test_noop_when_no_proc(self) -> None:
        scheduler = _make_scheduler()
        scheduler._proc = None

        asyncio.run(scheduler.kill_proc())

        assert scheduler.proc is None

    def test_noop_when_proc_already_exited(self) -> None:
        scheduler = _make_scheduler()
        proc = MagicMock()
        proc.returncode = 0
        proc.kill = MagicMock()
        scheduler._proc = proc

        asyncio.run(scheduler.kill_proc())

        proc.kill.assert_not_called()
        assert scheduler.proc is None


class TestMusicLoopLostWakeup:
    """MusicScheduler.loop must not block when mode is set before clear()."""

    def test_mode_on_before_wait_skips_blocking(self, tmp_path: Path) -> None:
        """If music_mode becomes 'on' between clear() and wait(), proceed."""
        scheduler = _make_scheduler(tmp_path)
        scheduler._mode = "on"
        scheduler._owner = "test-session"
        scheduler._vibe = ("focused", "[calm]")

        generation_happened = False

        async def fake_generate(
            self: object, prompt: str, duration_ms: int, output_path: Path
        ) -> Path:
            nonlocal generation_happened
            generation_happened = True
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"fake")
            scheduler._mode = "off"
            scheduler.changed.set()
            return output_path

        async def _drive() -> None:
            with patch(
                "punt_vox.providers.elevenlabs_music.ElevenLabsMusicProvider"
                ".generate_track",
                fake_generate,
            ):
                task = asyncio.create_task(MusicLoop(scheduler).run())
                await asyncio.sleep(0.1)
                if not generation_happened:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
                else:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task

        asyncio.run(_drive())

        assert generation_happened, (
            "music_loop blocked on wait despite music_mode=='on'"
        )


class TestGenFailureKeepsOldTrack:
    """Generation failure must not kill the old track subprocess."""

    def test_failure_then_success_old_track_alive_throughout(
        self, tmp_path: Path
    ) -> None:
        """First generation (vibe change) fails, retry succeeds."""
        scheduler = _make_scheduler(tmp_path)
        scheduler._mode = "on"
        scheduler._owner = "test-session"
        scheduler._vibe = ("focused", "[calm]")
        scheduler.changed.set()

        generation_count = 0
        old_proc_alive_snapshots: list[bool] = []

        async def fail_then_succeed(
            self: object, prompt: str, duration_ms: int, output_path: Path
        ) -> Path:
            nonlocal generation_count
            generation_count += 1
            output_path.parent.mkdir(parents=True, exist_ok=True)

            if generation_count == 2:
                proc = scheduler.proc
                old_proc_alive_snapshots.append(
                    proc is not None and proc.returncode is None,
                )
                msg = "network error"
                raise RuntimeError(msg)

            if generation_count == 3:
                proc = scheduler.proc
                old_proc_alive_snapshots.append(
                    proc is not None and proc.returncode is None,
                )

            output_path.write_bytes(b"fake-music-data")
            return output_path

        async def fake_subprocess(*args: object, **kwargs: object) -> MagicMock:
            proc = MagicMock()
            proc.returncode = None

            async def _wait() -> int:
                if proc.returncode is not None:
                    return int(proc.returncode)
                await asyncio.sleep(5.0)
                proc.returncode = 0
                return 0

            proc.wait = _wait
            proc.kill = MagicMock(
                side_effect=lambda: setattr(proc, "returncode", -9),
            )
            return proc

        async def _drive() -> None:
            with (
                patch(
                    "punt_vox.providers.elevenlabs_music."
                    "ElevenLabsMusicProvider.generate_track",
                    fail_then_succeed,
                ),
                patch(
                    "punt_vox.voxd.music.loop.asyncio.create_subprocess_exec",
                    fake_subprocess,
                ),
                patch.object(
                    MusicLoop,
                    "_backoff_sleep",
                    new=AsyncMock(),
                ),
            ):
                task = asyncio.create_task(MusicLoop(scheduler).run())

                for _ in range(100):
                    await asyncio.sleep(0.01)
                    if scheduler.proc is not None:
                        break

                scheduler._vibe = ("happy", "[warm]")
                scheduler.changed.set()

                for _ in range(100):
                    await asyncio.sleep(0.01)
                    if generation_count >= 3:
                        break

                scheduler._mode = "off"
                scheduler.changed.set()
                for _ in range(50):
                    await asyncio.sleep(0.01)
                    if scheduler.state == "idle":
                        break
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        asyncio.run(_drive())

        assert generation_count >= 3, (
            f"expected >=3 generations, got {generation_count}"
        )
        assert len(old_proc_alive_snapshots) >= 2, (
            f"expected >=2 liveness snapshots, got {old_proc_alive_snapshots}"
        )
        assert all(old_proc_alive_snapshots), (
            f"old track was killed during gen failure/retry: {old_proc_alive_snapshots}"
        )

    def test_max_retries_stops_music_mode(self, tmp_path: Path) -> None:
        """After max retries during playback, music_mode becomes 'off'."""
        scheduler = _make_scheduler(tmp_path)
        scheduler._mode = "on"
        scheduler._owner = "test-session"
        scheduler._vibe = ("focused", "[calm]")
        scheduler.changed.set()

        generation_count = 0

        async def always_fail_after_first(
            self: object, prompt: str, duration_ms: int, output_path: Path
        ) -> Path:
            nonlocal generation_count
            generation_count += 1
            output_path.parent.mkdir(parents=True, exist_ok=True)

            if generation_count == 1:
                output_path.write_bytes(b"fake-music-data")
                return output_path

            msg = f"generation failed (attempt {generation_count})"
            raise RuntimeError(msg)

        async def fake_subprocess(*args: object, **kwargs: object) -> MagicMock:
            proc = MagicMock()
            proc.returncode = None

            async def _wait() -> int:
                if proc.returncode is not None:
                    return int(proc.returncode)
                await asyncio.sleep(5.0)
                proc.returncode = 0
                return 0

            proc.wait = _wait
            proc.kill = MagicMock(
                side_effect=lambda: setattr(proc, "returncode", -9),
            )
            return proc

        async def _drive() -> None:
            with (
                patch(
                    "punt_vox.providers.elevenlabs_music."
                    "ElevenLabsMusicProvider.generate_track",
                    always_fail_after_first,
                ),
                patch(
                    "punt_vox.voxd.music.loop.asyncio.create_subprocess_exec",
                    fake_subprocess,
                ),
                patch.object(
                    MusicLoop,
                    "_backoff_sleep",
                    new=AsyncMock(),
                ),
            ):
                task = asyncio.create_task(MusicLoop(scheduler).run())

                for _ in range(100):
                    await asyncio.sleep(0.01)
                    if scheduler.proc is not None:
                        break

                scheduler._vibe = ("happy", "[warm]")
                scheduler.changed.set()

                for _ in range(100):
                    await asyncio.sleep(0.01)
                    if scheduler.mode == "off":
                        break

                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        asyncio.run(_drive())

        assert scheduler.mode == "off"
        assert scheduler.state == "idle"
        assert generation_count == 4, (
            f"expected 4 generations (1 ok + 3 fail), got {generation_count}"
        )

    def test_successful_handoff_after_retry_resets_counter(
        self, tmp_path: Path
    ) -> None:
        """A successful handoff after one failure resets the retry counter."""
        scheduler = _make_scheduler(tmp_path)
        scheduler._mode = "on"
        scheduler._owner = "test-session"
        scheduler._vibe = ("focused", "[calm]")
        scheduler.changed.set()

        generation_count = 0

        async def fail_once_per_cycle(
            self: object, prompt: str, duration_ms: int, output_path: Path
        ) -> Path:
            nonlocal generation_count
            generation_count += 1
            output_path.parent.mkdir(parents=True, exist_ok=True)

            if generation_count in (2, 4):
                msg = "transient error"
                raise RuntimeError(msg)

            output_path.write_bytes(b"fake-music-data")
            return output_path

        async def fake_subprocess(*args: object, **kwargs: object) -> MagicMock:
            proc = MagicMock()
            proc.returncode = None

            async def _wait() -> int:
                if proc.returncode is not None:
                    return int(proc.returncode)
                await asyncio.sleep(5.0)
                proc.returncode = 0
                return 0

            proc.wait = _wait
            proc.kill = MagicMock(
                side_effect=lambda: setattr(proc, "returncode", -9),
            )
            return proc

        async def _drive() -> None:
            with (
                patch(
                    "punt_vox.providers.elevenlabs_music."
                    "ElevenLabsMusicProvider.generate_track",
                    fail_once_per_cycle,
                ),
                patch(
                    "punt_vox.voxd.music.loop.asyncio.create_subprocess_exec",
                    fake_subprocess,
                ),
                patch.object(
                    MusicLoop,
                    "_backoff_sleep",
                    new=AsyncMock(),
                ),
            ):
                task = asyncio.create_task(MusicLoop(scheduler).run())

                for _ in range(100):
                    await asyncio.sleep(0.01)
                    if scheduler.proc is not None:
                        break

                scheduler._vibe = ("happy", "[warm]")
                scheduler.changed.set()

                for _ in range(100):
                    await asyncio.sleep(0.01)
                    if generation_count >= 3:
                        break

                assert scheduler.mode == "on"

                scheduler._vibe = ("energetic", "[bold]")
                scheduler.changed.set()

                for _ in range(100):
                    await asyncio.sleep(0.01)
                    if generation_count >= 5:
                        break

                assert scheduler.mode == "on", (
                    "retry counter was not reset: second failure cycle "
                    "pushed past max retries"
                )

                scheduler._mode = "off"
                scheduler.changed.set()
                for _ in range(50):
                    await asyncio.sleep(0.01)
                    if scheduler.state == "idle":
                        break
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        asyncio.run(_drive())

        assert generation_count >= 5, (
            f"expected >=5 generations (2 cycles of fail+succeed), "
            f"got {generation_count}"
        )
