"""Tests for the Sleeper backoff seam."""

from __future__ import annotations

from punt_vox.voxd.programs.sleeper import RealSleeper


async def test_real_sleeper_returns_after_sleeping() -> None:
    # A zero-second sleep exercises the real event-loop path without a real wait.
    await RealSleeper().sleep(0)
