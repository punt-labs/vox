"""Tests for punt_vox.voxd DaemonContext and legacy wrappers."""
# pyright: reportPrivateUsage=false

from __future__ import annotations

from punt_vox.voxd import (
    DaemonContext,
    _auto_track_name,
)


def _make_ctx() -> DaemonContext:
    """Build a DaemonContext without touching real files or auth."""
    return DaemonContext(auth_token=None, port=0)


class TestAutoTrackName:
    """_auto_track_name derives vibe-style-YYYYMMDD-HHMM patterns."""

    def test_with_vibe_and_style(self) -> None:
        ctx = _make_ctx()
        ctx.music_vibe = ("happy", "[warm]")
        ctx.music_style = "techno"
        name = _auto_track_name(ctx)
        # Name has vibe-style-YYYYMMDD-HHMM structure.
        assert name.startswith("happy-techno-")
        # Suffix is YYYYMMDD-HHMM: 8 digits, dash, 4 digits.
        parts = name.split("-")
        assert len(parts[-2]) == 8  # YYYYMMDD
        assert len(parts[-1]) == 4  # HHMM

    def test_no_vibe_uses_ambient(self) -> None:
        ctx = _make_ctx()
        ctx.music_vibe = ("", "")
        ctx.music_style = ""
        name = _auto_track_name(ctx)
        assert name.startswith("ambient-mix-")

    def test_no_style_uses_mix(self) -> None:
        ctx = _make_ctx()
        ctx.music_vibe = ("chill", "")
        ctx.music_style = ""
        name = _auto_track_name(ctx)
        assert name.startswith("chill-mix-")


class TestDaemonContextTrackName:
    """DaemonContext.music_track_name defaults to empty string."""

    def test_default(self) -> None:
        ctx = _make_ctx()
        assert ctx.music_track_name == ""

    def test_music_replay_default(self) -> None:
        ctx = _make_ctx()
        assert ctx.music_replay is False
