"""Tests for punt_vox.voxd.music_scheduler -- MusicScheduler extraction."""
# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from punt_vox.voxd import (
    DaemonContext,
    _kill_music_proc,
    _music_loop,
)
from punt_vox.voxd.music_scheduler import MusicScheduler
from punt_vox.voxd.track_generator import TrackGenerator


def _make_ctx(tmp_path: Path | None = None) -> DaemonContext:
    """Build a DaemonContext without touching real files or auth.

    When *tmp_path* is provided, the MusicScheduler's TrackGenerator
    writes to that directory instead of the real music output dir.
    """
    if tmp_path is not None:
        gen = TrackGenerator(tmp_path)
        scheduler = MusicScheduler(gen)
        return DaemonContext(auth_token=None, port=0, music=scheduler)
    with patch(
        "punt_vox.voxd._monolith._music_output_dir",
        return_value=Path("/tmp/vox-test-music"),
    ):
        return DaemonContext(auth_token=None, port=0)


class TestDaemonContextMusicFields:
    """DaemonContext must expose all music fields via delegation to MusicScheduler."""

    def test_music_mode_default(self) -> None:
        ctx = _make_ctx()
        assert ctx.music_mode == "off"

    def test_music_style_default(self) -> None:
        ctx = _make_ctx()
        assert ctx.music_style == ""

    def test_music_owner_default(self) -> None:
        ctx = _make_ctx()
        assert ctx.music_owner == ""

    def test_music_vibe_default(self) -> None:
        ctx = _make_ctx()
        assert ctx.music_vibe == ("", "")

    def test_music_track_default(self) -> None:
        ctx = _make_ctx()
        assert ctx.music_track is None

    def test_music_proc_default(self) -> None:
        ctx = _make_ctx()
        assert ctx.music_proc is None

    def test_music_state_default(self) -> None:
        ctx = _make_ctx()
        assert ctx.music_state == "idle"

    def test_music_changed_default(self) -> None:
        ctx = _make_ctx()
        assert isinstance(ctx.music_changed, asyncio.Event)
        assert not ctx.music_changed.is_set()

    def test_delegation_round_trips(self) -> None:
        """Setting via ctx.music_X reads back through the scheduler."""
        ctx = _make_ctx()
        ctx.music_mode = "on"
        assert ctx._music.mode == "on"
        ctx.music_style = "jazz"
        assert ctx._music.style == "jazz"
        ctx.music_owner = "sess-1"
        assert ctx._music.owner == "sess-1"
        ctx.music_vibe = ("chill", "[mellow]")
        assert ctx._music.vibe == ("chill", "[mellow]")
        ctx.music_track_name = "my-track"
        assert ctx._music.track_name == "my-track"
        ctx.music_replay = True
        assert ctx._music.replay is True
        ctx.music_state = "playing"
        assert ctx._music.state == "playing"


class TestMusicLoopStateTransitions:
    """MusicScheduler.loop: generation, playback, vibe changes, crash recovery."""

    def test_generates_and_plays_then_stops_on_off(self, tmp_path: Path) -> None:
        """Full cycle: mode on -> generate -> play -> mode off."""
        ctx = _make_ctx(tmp_path)

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
                    "punt_vox.voxd.music_scheduler.asyncio.create_subprocess_exec",
                    fake_subprocess,
                ),
            ):
                task = asyncio.create_task(_music_loop(ctx))
                await asyncio.sleep(0)

                # Turn music on.
                ctx.music_mode = "on"
                ctx.music_owner = "test-session"
                ctx.music_vibe = ("focused", "[calm]")
                ctx.music_changed.set()
                await asyncio.sleep(0.05)

                # Turn music off.
                ctx.music_mode = "off"
                ctx.music_changed.set()
                await asyncio.sleep(0.05)

                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        asyncio.run(_drive())

        assert "generate" in call_log
        assert "play" in call_log

    def test_crash_recovery_retries_with_backoff(self, tmp_path: Path) -> None:
        """Three failures in a row disable music mode."""
        ctx = _make_ctx(tmp_path)
        ctx.music_mode = "on"
        ctx.music_owner = "test-session"
        ctx.music_vibe = ("focused", "")
        ctx.music_changed.set()

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
                    MusicScheduler,
                    "_backoff_sleep",
                    new=AsyncMock(),
                ),
            ):
                task = asyncio.create_task(ctx._music.loop())
                # Yield control so the loop can run its 3 retries.
                for _ in range(20):
                    await asyncio.sleep(0)
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        asyncio.run(_drive())

        assert attempt_count == 3
        assert ctx.music_mode == "off"
        assert ctx.music_state == "idle"

    def test_vibe_change_during_generation_triggers_regeneration(
        self, tmp_path: Path
    ) -> None:
        """Setting music_changed during generation causes a new track."""
        ctx = _make_ctx(tmp_path)
        ctx.music_mode = "on"
        ctx.music_owner = "test-session"
        ctx.music_vibe = ("focused", "")
        ctx.music_changed.set()

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
                ctx.music_vibe = ("happy", "[warm]")
                ctx.music_changed.set()

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
                    "punt_vox.voxd.music_scheduler.asyncio.create_subprocess_exec",
                    fake_subprocess,
                ),
            ):
                task = asyncio.create_task(ctx._music.loop())
                await asyncio.sleep(0.15)

                ctx.music_mode = "off"
                ctx.music_changed.set()
                await asyncio.sleep(0.05)

                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        asyncio.run(_drive())

        assert generation_count >= 2


class TestMusicLoopGaplessHandoff:
    """Old track must keep looping while generation runs concurrently."""

    def test_old_track_loops_during_generation(self, tmp_path: Path) -> None:
        """Playback subprocess stays alive the entire time generation runs.

        Simulates a slow generation (~0.15s) and verifies the playback
        subprocess is NOT killed until the new track is ready.
        """
        ctx = _make_ctx(tmp_path)
        ctx.music_mode = "on"
        ctx.music_owner = "test-session"
        ctx.music_vibe = ("focused", "[calm]")
        ctx.music_changed.set()

        generation_count = 0
        play_count = 0
        # Track which procs were alive during generation.
        procs_alive_during_gen: list[bool] = []

        async def slow_generate(
            self: object, prompt: str, duration_ms: int, output_path: Path
        ) -> Path:
            nonlocal generation_count
            generation_count += 1
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"fake-music-data")

            if generation_count == 2:
                # Second generation (triggered by vibe change). The old
                # playback proc should still be alive during this window.
                proc = ctx.music_proc
                is_alive = proc is not None and proc.returncode is None
                procs_alive_during_gen.append(is_alive)
                # Simulate slow generation.
                await asyncio.sleep(0.1)
                # Check again after the sleep.
                proc = ctx.music_proc
                is_alive = proc is not None and proc.returncode is None
                procs_alive_during_gen.append(is_alive)

            return output_path

        async def fake_subprocess(*args: object, **kwargs: object) -> MagicMock:
            nonlocal play_count
            play_count += 1
            proc = MagicMock()
            proc.returncode = None  # Still running.

            async def _wait() -> int:
                # Simulate a long track so it doesn't end naturally.
                # Return immediately if the process was already killed,
                # mirroring real OS behavior after SIGKILL.
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
                    "punt_vox.voxd.music_scheduler.asyncio.create_subprocess_exec",
                    fake_subprocess,
                ),
            ):
                task = asyncio.create_task(ctx._music.loop())
                # Let initial generation + first playback start.
                await asyncio.sleep(0.05)

                # Trigger a vibe change while the first track is playing.
                ctx.music_vibe = ("happy", "[warm]")
                ctx.music_changed.set()

                # Wait for second generation to complete + handoff.
                await asyncio.sleep(0.3)

                # Shut down.
                ctx.music_mode = "off"
                ctx.music_changed.set()
                await asyncio.sleep(0.05)
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        asyncio.run(_drive())

        assert generation_count >= 2, (
            f"expected >=2 generations, got {generation_count}"
        )
        assert play_count >= 2, f"expected >=2 playback spawns, got {play_count}"
        # The old playback proc was alive during the entire generation window.
        assert all(procs_alive_during_gen), (
            f"old track was killed during generation: {procs_alive_during_gen}"
        )

    def test_second_vibe_change_cancels_inflight_generation(
        self, tmp_path: Path
    ) -> None:
        """A second vibe change during generation cancels the first and starts fresh."""
        ctx = _make_ctx(tmp_path)
        ctx.music_mode = "on"
        ctx.music_owner = "test-session"
        ctx.music_vibe = ("focused", "[calm]")
        ctx.music_changed.set()

        generation_vibes: list[str] = []
        gen_event = asyncio.Event()

        async def tracking_generate(
            self: object, prompt: str, duration_ms: int, output_path: Path
        ) -> Path:
            vibe, _ = ctx.music_vibe
            generation_vibes.append(vibe)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"fake-music-data")

            if len(generation_vibes) == 2:
                # Signal that second generation started, then simulate slow work.
                gen_event.set()
                await asyncio.sleep(0.5)
            elif len(generation_vibes) == 3:
                # Third generation -- the replacement after cancel.
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
                    "punt_vox.voxd.music_scheduler.asyncio.create_subprocess_exec",
                    fake_subprocess,
                ),
            ):
                task = asyncio.create_task(ctx._music.loop())
                await asyncio.sleep(0.05)

                # First vibe change triggers generation #2.
                ctx.music_vibe = ("happy", "[warm]")
                ctx.music_changed.set()
                # Wait for second generation to start.
                await asyncio.wait_for(gen_event.wait(), timeout=1.0)

                # Second vibe change while #2 is in-flight -- should cancel it.
                ctx.music_vibe = ("energetic", "[upbeat]")
                ctx.music_changed.set()
                await asyncio.sleep(0.3)

                ctx.music_mode = "off"
                ctx.music_changed.set()
                await asyncio.sleep(0.05)
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        asyncio.run(_drive())

        assert len(generation_vibes) >= 3, (
            f"expected >=3 generation attempts, got "
            f"{len(generation_vibes)}: {generation_vibes}"
        )
        # The third generation should have the latest vibe.
        assert generation_vibes[-1] == "energetic"


class TestKillMusicProc:
    """MusicScheduler.kill_proc safely terminates the music subprocess."""

    def test_kills_running_proc(self) -> None:
        ctx = _make_ctx()
        proc = MagicMock()
        proc.returncode = None
        proc.kill = MagicMock()
        proc.wait = AsyncMock(return_value=0)
        ctx.music_proc = proc

        asyncio.run(_kill_music_proc(ctx))

        proc.kill.assert_called_once()
        assert ctx.music_proc is None

    def test_noop_when_no_proc(self) -> None:
        ctx = _make_ctx()
        ctx.music_proc = None

        asyncio.run(_kill_music_proc(ctx))

        assert ctx.music_proc is None

    def test_noop_when_proc_already_exited(self) -> None:
        ctx = _make_ctx()
        proc = MagicMock()
        proc.returncode = 0
        proc.kill = MagicMock()
        ctx.music_proc = proc

        asyncio.run(_kill_music_proc(ctx))

        proc.kill.assert_not_called()
        assert ctx.music_proc is None


class TestMusicLoopLostWakeup:
    """MusicScheduler.loop must not block when mode is set before clear()."""

    def test_mode_on_before_wait_skips_blocking(self, tmp_path: Path) -> None:
        """If music_mode becomes 'on' between clear() and wait(), proceed."""
        ctx = _make_ctx(tmp_path)
        # Pre-set music_mode to "on" so the re-check after clear() catches it.
        ctx.music_mode = "on"
        ctx.music_owner = "test-session"
        ctx.music_vibe = ("focused", "[calm]")
        # Do NOT set music_changed -- the loop must detect mode via re-check.

        generation_happened = False

        async def fake_generate(
            self: object, prompt: str, duration_ms: int, output_path: Path
        ) -> Path:
            nonlocal generation_happened
            generation_happened = True
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"fake")
            # Turn off to let the loop exit cleanly.
            ctx.music_mode = "off"
            ctx.music_changed.set()
            return output_path

        async def _drive() -> None:
            with patch(
                "punt_vox.providers.elevenlabs_music.ElevenLabsMusicProvider"
                ".generate_track",
                fake_generate,
            ):
                task = asyncio.create_task(ctx._music.loop())
                # Give the loop enough time to either proceed or block.
                await asyncio.sleep(0.1)
                if not generation_happened:
                    # Loop is stuck -- cancel and fail.
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
    """Generation failure must not kill the old track subprocess.

    Covers the fix for issue vox-m2l: when the generation task fails
    during the playback loop, the old track keeps looping during
    retry/backoff. Only max-retries or a successful handoff kills it.
    """

    def test_failure_then_success_old_track_alive_throughout(
        self, tmp_path: Path
    ) -> None:
        """First generation (vibe change) fails, retry succeeds.

        The old playback subprocess must remain alive (returncode is
        None) during the entire failure + backoff + retry window.
        """
        ctx = _make_ctx(tmp_path)
        ctx.music_mode = "on"
        ctx.music_owner = "test-session"
        ctx.music_vibe = ("focused", "[calm]")
        ctx.music_changed.set()

        generation_count = 0
        # Snapshots of old-proc liveness taken during the failing gen
        # and during the retry gen.
        old_proc_alive_snapshots: list[bool] = []

        async def fail_then_succeed(
            self: object, prompt: str, duration_ms: int, output_path: Path
        ) -> Path:
            nonlocal generation_count
            generation_count += 1
            output_path.parent.mkdir(parents=True, exist_ok=True)

            if generation_count == 2:
                # Second generation (triggered by vibe change): FAIL.
                # Snapshot old proc liveness before raising.
                proc = ctx.music_proc
                old_proc_alive_snapshots.append(
                    proc is not None and proc.returncode is None,
                )
                msg = "network error"
                raise RuntimeError(msg)

            if generation_count == 3:
                # Third generation (retry after failure): succeed.
                # The old proc should STILL be alive during retry.
                proc = ctx.music_proc
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
                    "punt_vox.voxd.music_scheduler.asyncio.create_subprocess_exec",
                    fake_subprocess,
                ),
                patch.object(
                    MusicScheduler,
                    "_backoff_sleep",
                    new=AsyncMock(),
                ),
            ):
                task = asyncio.create_task(ctx._music.loop())

                # Poll until initial generation + first playback start.
                for _ in range(100):
                    await asyncio.sleep(0.01)
                    if ctx.music_proc is not None:
                        break

                # Trigger vibe change -- second generation will fail.
                ctx.music_vibe = ("happy", "[warm]")
                ctx.music_changed.set()

                # Poll until failure + backoff + retry + handoff complete.
                for _ in range(100):
                    await asyncio.sleep(0.01)
                    if generation_count >= 3:
                        break

                ctx.music_mode = "off"
                ctx.music_changed.set()
                for _ in range(50):
                    await asyncio.sleep(0.01)
                    if ctx.music_state == "idle":
                        break
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        asyncio.run(_drive())

        # Generation ran at least 3 times: initial, fail, retry.
        assert generation_count >= 3, (
            f"expected >=3 generations, got {generation_count}"
        )
        # Old track was alive during both the failing gen and the retry.
        assert len(old_proc_alive_snapshots) >= 2, (
            f"expected >=2 liveness snapshots, got {old_proc_alive_snapshots}"
        )
        assert all(old_proc_alive_snapshots), (
            f"old track was killed during gen failure/retry: {old_proc_alive_snapshots}"
        )

    def test_max_retries_stops_music_mode(self, tmp_path: Path) -> None:
        """After max retries during playback, music_mode becomes 'off'."""
        ctx = _make_ctx(tmp_path)
        ctx.music_mode = "on"
        ctx.music_owner = "test-session"
        ctx.music_vibe = ("focused", "[calm]")
        ctx.music_changed.set()

        generation_count = 0

        async def always_fail_after_first(
            self: object, prompt: str, duration_ms: int, output_path: Path
        ) -> Path:
            nonlocal generation_count
            generation_count += 1
            output_path.parent.mkdir(parents=True, exist_ok=True)

            if generation_count == 1:
                # Initial generation succeeds.
                output_path.write_bytes(b"fake-music-data")
                return output_path

            # All subsequent generations fail.
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
                    "punt_vox.voxd.music_scheduler.asyncio.create_subprocess_exec",
                    fake_subprocess,
                ),
                patch.object(
                    MusicScheduler,
                    "_backoff_sleep",
                    new=AsyncMock(),
                ),
            ):
                task = asyncio.create_task(ctx._music.loop())

                # Poll until initial generation + first playback start.
                for _ in range(100):
                    await asyncio.sleep(0.01)
                    if ctx.music_proc is not None:
                        break

                # Trigger vibe change -- all subsequent gens will fail.
                ctx.music_vibe = ("happy", "[warm]")
                ctx.music_changed.set()

                # Poll for 3 failures + final shutdown.
                for _ in range(100):
                    await asyncio.sleep(0.01)
                    if ctx.music_mode == "off":
                        break

                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        asyncio.run(_drive())

        assert ctx.music_mode == "off"
        assert ctx.music_state == "idle"
        # 1 initial success + 3 failures = 4 total.
        assert generation_count == 4, (
            f"expected 4 generations (1 ok + 3 fail), got {generation_count}"
        )

    def test_successful_handoff_after_retry_resets_counter(
        self, tmp_path: Path
    ) -> None:
        """A successful handoff after one failure resets the retry counter."""
        ctx = _make_ctx(tmp_path)
        ctx.music_mode = "on"
        ctx.music_owner = "test-session"
        ctx.music_vibe = ("focused", "[calm]")
        ctx.music_changed.set()

        generation_count = 0

        async def fail_once_per_cycle(
            self: object, prompt: str, duration_ms: int, output_path: Path
        ) -> Path:
            nonlocal generation_count
            generation_count += 1
            output_path.parent.mkdir(parents=True, exist_ok=True)

            # Fail once per vibe-change cycle: gen #2 and gen #4.
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
                    "punt_vox.voxd.music_scheduler.asyncio.create_subprocess_exec",
                    fake_subprocess,
                ),
                patch.object(
                    MusicScheduler,
                    "_backoff_sleep",
                    new=AsyncMock(),
                ),
            ):
                task = asyncio.create_task(ctx._music.loop())

                # Poll until initial generation + first playback start.
                for _ in range(100):
                    await asyncio.sleep(0.01)
                    if ctx.music_proc is not None:
                        break

                # Trigger vibe change -- gen #2 fails, #3 succeeds.
                ctx.music_vibe = ("happy", "[warm]")
                ctx.music_changed.set()

                # Poll until retry succeeds (generation_count >= 3).
                for _ in range(100):
                    await asyncio.sleep(0.01)
                    if generation_count >= 3:
                        break

                # Music should still be on -- the retry succeeded.
                assert ctx.music_mode == "on"

                # Now trigger a SECOND vibe change to prove the retry
                # counter was actually reset. gen #4 will fail, #5
                # will succeed. If the counter had accumulated from
                # the first failure cycle, this second failure would
                # push past max retries and turn music off.
                ctx.music_vibe = ("energetic", "[bold]")
                ctx.music_changed.set()

                for _ in range(100):
                    await asyncio.sleep(0.01)
                    if generation_count >= 5:
                        break

                # Music is STILL on -- the counter was reset by the
                # first successful handoff and the second cycle's
                # single failure did not exceed max retries.
                assert ctx.music_mode == "on", (
                    "retry counter was not reset: second failure cycle "
                    "pushed past max retries"
                )

                ctx.music_mode = "off"
                ctx.music_changed.set()
                for _ in range(50):
                    await asyncio.sleep(0.01)
                    if ctx.music_state == "idle":
                        break
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        asyncio.run(_drive())

        assert generation_count >= 5, (
            f"expected >=5 generations (2 cycles of fail+succeed), "
            f"got {generation_count}"
        )
        # Music stayed on through both failure+retry cycles.
