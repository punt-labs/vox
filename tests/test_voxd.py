"""Tests for punt_vox.voxd.track_generator -- auto_track_name."""
# pyright: reportPrivateUsage=false

from __future__ import annotations

from punt_vox.voxd import TrackGenerator


class TestAutoTrackName:
    """TrackGenerator.auto_track_name derives vibe-style-YYYYMMDD-HHMM patterns."""

    def _tg(self) -> TrackGenerator:
        from pathlib import Path

        return TrackGenerator(Path("/tmp/vox-test-music"))

    def test_with_vibe_and_style(self) -> None:
        name = self._tg().auto_track_name("happy", "techno")
        assert name.startswith("happy-techno-")
        parts = name.split("-")
        assert len(parts[-2]) == 8  # YYYYMMDD
        assert len(parts[-1]) == 4  # HHMM

    def test_no_vibe_uses_ambient(self) -> None:
        name = self._tg().auto_track_name("", "")
        assert name.startswith("ambient-mix-")

    def test_no_style_uses_mix(self) -> None:
        name = self._tg().auto_track_name("chill", "")
        assert name.startswith("chill-mix-")
