"""Tests for punt_vox.voxd.track_generator -- auto_track_name."""
# pyright: reportPrivateUsage=false

from __future__ import annotations

from pathlib import Path

from punt_vox.voxd import TrackGenerator
from punt_vox.voxd.music.store import FilesystemTrackStore


class TestAutoTrackName:
    """auto_track_name derives <vibe>_<style>_YYYYMMDD_HHMM_<counter> patterns."""

    def _tg(self, tmp_path: Path) -> TrackGenerator:
        return TrackGenerator(FilesystemTrackStore(tmp_path))

    def test_with_vibe_and_style(self, tmp_path: Path) -> None:
        name = self._tg(tmp_path).auto_track_name(
            TrackGenerator.pool_prefix(("happy", "techno"))
        )
        assert name.startswith("happy_techno_")
        parts = name.split("_")
        assert len(parts[-3]) == 8  # YYYYMMDD
        assert len(parts[-2]) == 4  # HHMM
        assert parts[-1] == "0"  # first free counter in an empty dir

    def test_no_vibe_uses_ambient(self, tmp_path: Path) -> None:
        name = self._tg(tmp_path).auto_track_name(TrackGenerator.pool_prefix(("", "")))
        assert name.startswith("ambient_mix_")

    def test_no_style_uses_mix(self, tmp_path: Path) -> None:
        name = self._tg(tmp_path).auto_track_name(
            TrackGenerator.pool_prefix(("chill", ""))
        )
        assert name.startswith("chill_mix_")
