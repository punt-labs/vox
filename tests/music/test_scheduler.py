"""Tests for punt_vox.voxd.music.scheduler -- domain methods."""
# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from punt_vox.voxd.music.generator import TrackGenerator
from punt_vox.voxd.music.scheduler import MusicScheduler
from punt_vox.voxd.music.types import MusicResponse

__all__: list[str] = []


def _make_scheduler(tmp_path: Path) -> MusicScheduler:
    """Build a MusicScheduler with a TrackGenerator writing to tmp_path."""
    gen = TrackGenerator(tmp_path)
    return MusicScheduler(gen)


class TestTurnOn:
    """MusicScheduler.turn_on sets state and signals changed."""

    def test_turn_on_sets_state(self, tmp_path: Path) -> None:
        scheduler = _make_scheduler(tmp_path)
        result = asyncio.run(
            scheduler.turn_on(
                owner_id="sess-1",
                style="techno",
                vibe=("focused", "[calm]"),
                name="",
            )
        )

        assert result == MusicResponse(status="generating")
        assert scheduler.mode == "on"
        assert scheduler.owner == "sess-1"
        assert scheduler.style == "techno"
        assert scheduler.vibe == ("focused", "[calm]")
        assert scheduler.state == "generating"
        assert scheduler.changed.is_set()

    def test_turn_on_rejects_empty_owner(self, tmp_path: Path) -> None:
        scheduler = _make_scheduler(tmp_path)
        with pytest.raises(ValueError, match="owner_id is required"):
            asyncio.run(
                scheduler.turn_on(
                    owner_id="",
                    style="techno",
                    vibe=("focused", "[calm]"),
                    name="",
                )
            )

    def test_turn_on_replay_existing_track(self, tmp_path: Path) -> None:
        music_dir = tmp_path / "music"
        music_dir.mkdir()
        track = music_dir / "my_focus.mp3"
        track.write_bytes(b"fake-music")

        gen = TrackGenerator(music_dir)
        scheduler = MusicScheduler(gen)
        result = asyncio.run(
            scheduler.turn_on(
                owner_id="sess-1",
                style="jazz",
                vibe=("happy", "[warm]"),
                name="my focus",
            )
        )

        assert result.status == "playing"
        assert result.track == str(track)
        assert result.name == "my_focus"
        assert scheduler.mode == "on"
        assert scheduler.track == track
        assert scheduler.track_name == "my_focus"
        assert scheduler.state == "playing"
        assert scheduler.replay is True

    def test_turn_on_invalid_track_name(self, tmp_path: Path) -> None:
        scheduler = _make_scheduler(tmp_path)
        with pytest.raises(ValueError, match="invalid track name"):
            asyncio.run(
                scheduler.turn_on(
                    owner_id="sess-1",
                    style="",
                    vibe=("", ""),
                    name="---",
                )
            )

    def test_turn_on_ownership_transfer_kills_proc(self, tmp_path: Path) -> None:
        scheduler = _make_scheduler(tmp_path)
        scheduler.mode = "on"
        scheduler.owner = "old-owner"

        fake_proc = MagicMock()
        fake_proc.returncode = None
        fake_proc.kill = MagicMock()
        fake_proc.wait = AsyncMock(return_value=0)
        scheduler.proc = fake_proc

        asyncio.run(
            scheduler.turn_on(
                owner_id="new-owner",
                style="",
                vibe=("happy", ""),
                name="",
            )
        )

        fake_proc.kill.assert_called_once()
        assert scheduler.owner == "new-owner"
        assert scheduler.proc is None

    def test_turn_on_same_owner_skips_kill(self, tmp_path: Path) -> None:
        scheduler = _make_scheduler(tmp_path)
        scheduler.mode = "on"
        scheduler.owner = "sess-1"

        fake_proc = MagicMock()
        fake_proc.returncode = None
        fake_proc.kill = MagicMock()
        fake_proc.wait = AsyncMock(return_value=0)
        scheduler.proc = fake_proc

        asyncio.run(
            scheduler.turn_on(
                owner_id="sess-1",
                style="jazz",
                vibe=("chill", "[mellow]"),
                name="",
            )
        )

        fake_proc.kill.assert_not_called()
        assert scheduler.mode == "on"
        assert scheduler.style == "jazz"

    def test_turn_on_preserves_existing_style(self, tmp_path: Path) -> None:
        scheduler = _make_scheduler(tmp_path)
        scheduler.style = "jazz"

        asyncio.run(
            scheduler.turn_on(
                owner_id="sess-1",
                style="",
                vibe=("focused", ""),
                name="",
            )
        )

        assert scheduler.style == "jazz"


class TestTurnOff:
    """MusicScheduler.turn_off resets state."""

    def test_turn_off_resets_state(self, tmp_path: Path) -> None:
        scheduler = _make_scheduler(tmp_path)
        scheduler.mode = "on"
        scheduler.state = "playing"
        scheduler.replay = True

        result = asyncio.run(scheduler.turn_off())

        assert result == MusicResponse(status="stopped")
        assert scheduler.mode == "off"
        assert scheduler.state == "idle"
        assert scheduler.replay is False
        assert scheduler.changed.is_set()

    def test_turn_off_kills_proc(self, tmp_path: Path) -> None:
        scheduler = _make_scheduler(tmp_path)
        fake_proc = MagicMock()
        fake_proc.returncode = None
        fake_proc.kill = MagicMock()
        fake_proc.wait = AsyncMock(return_value=0)
        scheduler.proc = fake_proc

        asyncio.run(scheduler.turn_off())

        fake_proc.kill.assert_called_once()
        assert scheduler.proc is None


class TestPlayTrack:
    """MusicScheduler.play_track replays saved tracks."""

    def test_play_track_replays(self, tmp_path: Path) -> None:
        music_dir = tmp_path / "music"
        music_dir.mkdir()
        track = music_dir / "chill_vibes.mp3"
        track.write_bytes(b"fake-music")

        gen = TrackGenerator(music_dir)
        scheduler = MusicScheduler(gen)
        result = asyncio.run(
            scheduler.play_track(name="chill vibes", owner_id="sess-1")
        )

        assert result.status == "playing"
        assert result.track == str(track)
        assert result.name == "chill_vibes"
        assert scheduler.mode == "on"
        assert scheduler.track == track
        assert scheduler.state == "playing"
        assert scheduler.replay is True
        assert scheduler.changed.is_set()

    def test_play_track_not_found(self, tmp_path: Path) -> None:
        music_dir = tmp_path / "music"
        music_dir.mkdir()

        gen = TrackGenerator(music_dir)
        scheduler = MusicScheduler(gen)

        with pytest.raises(ValueError, match="track not found"):
            asyncio.run(scheduler.play_track(name="nonexistent", owner_id="sess-1"))

    def test_play_track_empty_name(self, tmp_path: Path) -> None:
        scheduler = _make_scheduler(tmp_path)
        with pytest.raises(ValueError, match="name is required"):
            asyncio.run(scheduler.play_track(name="", owner_id="sess-1"))

    def test_play_track_empty_owner(self, tmp_path: Path) -> None:
        scheduler = _make_scheduler(tmp_path)
        with pytest.raises(ValueError, match="owner_id is required"):
            asyncio.run(scheduler.play_track(name="test", owner_id=""))


class TestUpdateVibe:
    """MusicScheduler.update_vibe checks ownership and signals changed."""

    def test_update_vibe_matching_owner(self, tmp_path: Path) -> None:
        scheduler = _make_scheduler(tmp_path)
        scheduler.owner = "sess-1"
        scheduler.vibe = ("old", "[old-tags]")

        result = scheduler.update_vibe(owner_id="sess-1", vibe=("happy", "[warm]"))

        assert result == MusicResponse(status="generating")
        assert scheduler.vibe == ("happy", "[warm]")
        assert scheduler.changed.is_set()

    def test_update_vibe_non_owner_ignored(self, tmp_path: Path) -> None:
        scheduler = _make_scheduler(tmp_path)
        scheduler.owner = "sess-1"
        scheduler.vibe = ("old", "[old-tags]")

        result = scheduler.update_vibe(owner_id="other-sess", vibe=("happy", "[warm]"))

        assert result == MusicResponse(status="ignored")
        assert scheduler.vibe == ("old", "[old-tags]")

    def test_update_vibe_same_vibe_ignored(self, tmp_path: Path) -> None:
        scheduler = _make_scheduler(tmp_path)
        scheduler.owner = "sess-1"
        scheduler.vibe = ("happy", "[warm]")

        result = scheduler.update_vibe(owner_id="sess-1", vibe=("happy", "[warm]"))

        assert result == MusicResponse(status="ignored")
        assert not scheduler.changed.is_set()

    def test_update_vibe_empty_owner(self, tmp_path: Path) -> None:
        scheduler = _make_scheduler(tmp_path)
        with pytest.raises(ValueError, match="owner_id is required"):
            scheduler.update_vibe(owner_id="", vibe=("happy", "[warm]"))


class TestSkipNext:
    """MusicScheduler.skip_next signals a new track."""

    def test_skip_next_signals_changed(self, tmp_path: Path) -> None:
        scheduler = _make_scheduler(tmp_path)
        scheduler.mode = "on"

        result = scheduler.skip_next(owner_id="sess-1")

        assert result == MusicResponse(status="generating")
        assert scheduler.changed.is_set()

    def test_skip_next_when_off_ignored(self, tmp_path: Path) -> None:
        scheduler = _make_scheduler(tmp_path)
        scheduler.mode = "off"

        result = scheduler.skip_next(owner_id="sess-1")

        assert result == MusicResponse(status="ignored")

    def test_skip_next_clears_replay(self, tmp_path: Path) -> None:
        scheduler = _make_scheduler(tmp_path)
        scheduler.mode = "on"
        scheduler.replay = True

        scheduler.skip_next(owner_id="sess-1")

        assert scheduler.replay is False
        assert scheduler.changed.is_set()

    def test_skip_next_empty_owner(self, tmp_path: Path) -> None:
        scheduler = _make_scheduler(tmp_path)
        with pytest.raises(ValueError, match="owner_id is required"):
            scheduler.skip_next(owner_id="")
