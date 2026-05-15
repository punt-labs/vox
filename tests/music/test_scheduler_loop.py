"""Tests for MusicScheduler.loop -- max retries disables music."""
# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from punt_vox.voxd.music.generator import TrackGenerator
from punt_vox.voxd.music.loop import MusicLoop, _MUSIC_MAX_RETRIES
from punt_vox.voxd.music.scheduler import MusicScheduler

__all__: list[str] = []


def _make_scheduler(tmp_path: Path) -> MusicScheduler:
    """Build a MusicScheduler with a TrackGenerator writing to tmp_path."""
    gen = TrackGenerator(tmp_path)
    return MusicScheduler(gen)


class TestMaxRetriesDisablesMusic:
    """After _MUSIC_MAX_RETRIES generation failures, mode should be 'off'."""

    def test_max_retries_disables_music(self, tmp_path: Path) -> None:
        """Consecutive generation failures during playback disable music."""
        scheduler = _make_scheduler(tmp_path)
        scheduler._mode = "on"
        scheduler._owner = "test-session"
        scheduler._vibe = ("focused", "[calm]")
        scheduler.changed.set()

        generation_count = 0

        async def always_fail_after_first(
            self: object,
            prompt: str,
            duration_ms: int,
            output_path: Path,
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
                    "punt_vox.voxd.music.scheduler.asyncio.create_subprocess_exec",
                    fake_subprocess,
                ),
                patch.object(
                    MusicLoop,
                    "_backoff_sleep",
                    new=AsyncMock(),
                ),
            ):
                loop = MusicLoop(scheduler)
                task = asyncio.create_task(loop.run())

                # Wait for initial generation + playback to start.
                for _ in range(100):
                    await asyncio.sleep(0.01)
                    if scheduler.proc is not None:
                        break

                # Trigger vibe change to start parallel generation.
                scheduler._vibe = ("happy", "[warm]")
                scheduler.changed.set()

                # Wait for max retries to disable music.
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
        # 1 successful initial + _MUSIC_MAX_RETRIES failures.
        assert generation_count == 1 + _MUSIC_MAX_RETRIES
