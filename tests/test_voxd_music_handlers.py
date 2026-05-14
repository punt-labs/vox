"""Tests for punt_vox.voxd.music_handlers -- music playback handlers."""
# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from punt_vox.voxd.music_handlers import MusicHandlers
from punt_vox.voxd.music_scheduler import MusicScheduler
from punt_vox.voxd.track_generator import TrackGenerator


def _make_music_handlers_and_scheduler(
    *,
    track_generator: TrackGenerator | None = None,
) -> tuple[MusicScheduler, MusicHandlers]:
    """Build a MusicScheduler and MusicHandlers pair for testing."""
    from punt_vox.dirs import music_output_dir

    tg = track_generator or TrackGenerator(music_output_dir())
    ms = MusicScheduler(tg)
    handlers = MusicHandlers(music=ms, track_generator=tg)
    return ms, handlers


class TestMusicHandlerRegistration:
    """Music handler methods exist on MusicHandlers."""

    def test_handle_music_on_exists(self) -> None:
        _ms, handlers = _make_music_handlers_and_scheduler()
        assert callable(handlers.handle_music_on)

    def test_handle_music_off_exists(self) -> None:
        _ms, handlers = _make_music_handlers_and_scheduler()
        assert callable(handlers.handle_music_off)

    def test_handle_music_vibe_exists(self) -> None:
        _ms, handlers = _make_music_handlers_and_scheduler()
        assert callable(handlers.handle_music_vibe)


class TestHandleMusicOn:
    """MusicHandlers.handle_music_on: ownership transfer and state mutation."""

    def test_sets_music_mode_and_owner(self) -> None:
        music, handlers = _make_music_handlers_and_scheduler()
        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {
            "id": "req-1",
            "owner_id": "session-abc",
            "style": "techno",
            "vibe": "focused",
            "vibe_tags": "[calm]",
        }

        asyncio.run(handlers.handle_music_on(msg, ws))

        assert music.mode == "on"
        assert music.owner == "session-abc"
        assert music.style == "techno"
        assert music.vibe == ("focused", "[calm]")
        assert music.state == "generating"
        assert music.changed.is_set()

    def test_responds_with_generating_status(self) -> None:
        _music, handlers = _make_music_handlers_and_scheduler()
        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {
            "id": "req-2",
            "owner_id": "session-xyz",
        }

        asyncio.run(handlers.handle_music_on(msg, ws))

        ws.send_json.assert_called_once_with(
            {"type": "music_on", "id": "req-2", "status": "generating"}
        )

    def test_ownership_transfer_kills_existing_proc(self) -> None:
        """Transferring ownership kills the previous subprocess."""
        music, handlers = _make_music_handlers_and_scheduler()
        music.mode = "on"
        music.owner = "old-session"

        fake_proc = MagicMock()
        fake_proc.returncode = None
        fake_proc.kill = MagicMock()
        fake_proc.wait = AsyncMock(return_value=0)
        music.proc = fake_proc

        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {
            "id": "req-3",
            "owner_id": "new-session",
            "vibe": "happy",
            "vibe_tags": "[warm]",
        }

        asyncio.run(handlers.handle_music_on(msg, ws))

        fake_proc.kill.assert_called_once()
        assert music.owner == "new-session"
        assert music.proc is None

    def test_preserves_existing_style_when_not_provided(self) -> None:
        music, handlers = _make_music_handlers_and_scheduler()
        music.style = "jazz"
        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {
            "id": "req-4",
            "owner_id": "session-1",
            "style": "",
            "vibe": "focused",
        }

        asyncio.run(handlers.handle_music_on(msg, ws))

        assert music.style == "jazz"


class TestHandleMusicOff:
    """MusicHandlers.handle_music_off: stops music and resets state."""

    def test_sets_mode_off_and_state_idle(self) -> None:
        music, handlers = _make_music_handlers_and_scheduler()
        music.mode = "on"
        music.state = "playing"
        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {"id": "req-off"}

        asyncio.run(handlers.handle_music_off(msg, ws))

        assert music.mode == "off"
        assert music.state == "idle"
        assert music.changed.is_set()

    def test_responds_with_stopped_status(self) -> None:
        _music, handlers = _make_music_handlers_and_scheduler()
        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {"id": "req-off-2"}

        asyncio.run(handlers.handle_music_off(msg, ws))

        ws.send_json.assert_called_once_with(
            {"type": "music_off", "id": "req-off-2", "status": "stopped"}
        )

    def test_kills_running_subprocess(self) -> None:
        music, handlers = _make_music_handlers_and_scheduler()
        fake_proc = MagicMock()
        fake_proc.returncode = None
        fake_proc.kill = MagicMock()
        fake_proc.wait = AsyncMock(return_value=0)
        music.proc = fake_proc

        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {"id": "req-off-3"}

        asyncio.run(handlers.handle_music_off(msg, ws))

        fake_proc.kill.assert_called_once()
        assert music.proc is None


class TestHandleMusicVibe:
    """MusicHandlers.handle_music_vibe: ownership check and vibe update."""

    def test_matching_owner_updates_vibe(self) -> None:
        music, handlers = _make_music_handlers_and_scheduler()
        music.mode = "on"
        music.owner = "session-abc"
        music.vibe = ("old", "[old-tags]")
        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {
            "id": "vibe-1",
            "owner_id": "session-abc",
            "vibe": "happy",
            "vibe_tags": "[warm]",
        }

        asyncio.run(handlers.handle_music_vibe(msg, ws))

        assert music.vibe == ("happy", "[warm]")
        assert music.changed.is_set()
        ws.send_json.assert_called_once_with(
            {"type": "music_vibe", "id": "vibe-1", "status": "generating"}
        )

    def test_non_owner_rejected(self) -> None:
        music, handlers = _make_music_handlers_and_scheduler()
        music.mode = "on"
        music.owner = "session-abc"
        music.vibe = ("old", "[old-tags]")
        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {
            "id": "vibe-2",
            "owner_id": "session-other",
            "vibe": "happy",
            "vibe_tags": "[warm]",
        }

        asyncio.run(handlers.handle_music_vibe(msg, ws))

        assert music.vibe == ("old", "[old-tags]")
        ws.send_json.assert_called_once_with(
            {"type": "music_vibe", "id": "vibe-2", "status": "ignored"}
        )

    def test_same_vibe_ignored(self) -> None:
        music, handlers = _make_music_handlers_and_scheduler()
        music.owner = "session-abc"
        music.vibe = ("happy", "[warm]")
        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {
            "id": "vibe-3",
            "owner_id": "session-abc",
            "vibe": "happy",
            "vibe_tags": "[warm]",
        }

        asyncio.run(handlers.handle_music_vibe(msg, ws))

        ws.send_json.assert_called_once_with(
            {"type": "music_vibe", "id": "vibe-3", "status": "ignored"}
        )
        assert not music.changed.is_set()


class TestHandleMusicOnWhilePlaying:
    """MusicHandlers.handle_music_on: gapless handoff when music is already playing."""

    def test_same_owner_skips_kill(self) -> None:
        """Re-sending music_on while playing (same owner) does not kill proc."""
        music, handlers = _make_music_handlers_and_scheduler()
        music.mode = "on"
        music.owner = "session-abc"

        fake_proc = MagicMock()
        fake_proc.returncode = None
        fake_proc.kill = MagicMock()
        fake_proc.wait = AsyncMock(return_value=0)
        music.proc = fake_proc

        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {
            "id": "req-gapless",
            "owner_id": "session-abc",
            "style": "jazz",
            "vibe": "chill",
            "vibe_tags": "[mellow]",
        }

        asyncio.run(handlers.handle_music_on(msg, ws))

        fake_proc.kill.assert_not_called()
        assert music.mode == "on"
        assert music.style == "jazz"
        assert music.vibe == ("chill", "[mellow]")
        assert music.changed.is_set()

    def test_different_owner_kills_proc(self) -> None:
        """Ownership transfer while playing kills the existing proc."""
        music, handlers = _make_music_handlers_and_scheduler()
        music.mode = "on"
        music.owner = "old-owner"

        fake_proc = MagicMock()
        fake_proc.returncode = None
        fake_proc.kill = MagicMock()
        fake_proc.wait = AsyncMock(return_value=0)
        music.proc = fake_proc

        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {
            "id": "req-transfer",
            "owner_id": "new-owner",
            "vibe": "upbeat",
            "vibe_tags": "[energetic]",
        }

        asyncio.run(handlers.handle_music_on(msg, ws))

        fake_proc.kill.assert_called_once()
        assert music.owner == "new-owner"
        assert music.proc is None


class TestHandleMusicNext:
    """MusicHandlers.handle_music_next: skip-track handler tests."""

    def test_signals_music_changed(self) -> None:
        music, handlers = _make_music_handlers_and_scheduler()
        music.mode = "on"
        music.owner = "session-abc"
        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {
            "id": "next-1",
            "owner_id": "session-abc",
        }

        asyncio.run(handlers.handle_music_next(msg, ws))

        assert music.changed.is_set()
        ws.send_json.assert_called_once_with(
            {"type": "music_next", "id": "next-1", "status": "generating"}
        )

    def test_ignored_when_music_off(self) -> None:
        music, handlers = _make_music_handlers_and_scheduler()
        music.mode = "off"
        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {
            "id": "next-2",
            "owner_id": "session-abc",
        }

        asyncio.run(handlers.handle_music_next(msg, ws))

        assert not music.changed.is_set()
        ws.send_json.assert_called_once_with(
            {"type": "music_next", "id": "next-2", "status": "ignored"}
        )

    def test_clears_replay_flag(self) -> None:
        music, handlers = _make_music_handlers_and_scheduler()
        music.mode = "on"
        music.owner = "session-abc"
        music.replay = True
        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {
            "id": "next-3",
            "owner_id": "session-abc",
        }

        asyncio.run(handlers.handle_music_next(msg, ws))

        assert music.replay is False
        assert music.changed.is_set()

    def test_error_when_no_owner_id(self) -> None:
        music, handlers = _make_music_handlers_and_scheduler()
        music.mode = "on"
        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {"id": "next-4"}

        asyncio.run(handlers.handle_music_next(msg, ws))

        ws.send_json.assert_called_once_with(
            {"type": "error", "id": "next-4", "message": "owner_id is required"}
        )


class TestEmptyOwnerIdRejection:
    """Handlers must reject empty owner_id to prevent ownership spoofing."""

    def test_music_on_rejects_empty_owner_id(self) -> None:
        music, handlers = _make_music_handlers_and_scheduler()
        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {"id": "empty-1", "owner_id": "", "vibe": "focused"}

        asyncio.run(handlers.handle_music_on(msg, ws))

        ws.send_json.assert_called_once_with(
            {"type": "error", "id": "empty-1", "message": "owner_id is required"}
        )
        assert music.mode == "off"

    def test_music_on_rejects_missing_owner_id(self) -> None:
        music, handlers = _make_music_handlers_and_scheduler()
        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {"id": "empty-2", "vibe": "focused"}

        asyncio.run(handlers.handle_music_on(msg, ws))

        ws.send_json.assert_called_once_with(
            {"type": "error", "id": "empty-2", "message": "owner_id is required"}
        )
        assert music.mode == "off"

    def test_music_vibe_rejects_empty_owner_id(self) -> None:
        music, handlers = _make_music_handlers_and_scheduler()
        music.mode = "on"
        music.owner = "real-session"
        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {
            "id": "empty-3",
            "owner_id": "",
            "vibe": "happy",
        }

        asyncio.run(handlers.handle_music_vibe(msg, ws))

        ws.send_json.assert_called_once_with(
            {"type": "error", "id": "empty-3", "message": "owner_id is required"}
        )
        assert music.vibe == ("", "")

    def test_music_vibe_rejects_missing_owner_id(self) -> None:
        music, handlers = _make_music_handlers_and_scheduler()
        music.mode = "on"
        music.owner = "real-session"
        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {"id": "empty-4", "vibe": "happy"}

        asyncio.run(handlers.handle_music_vibe(msg, ws))

        ws.send_json.assert_called_once_with(
            {"type": "error", "id": "empty-4", "message": "owner_id is required"}
        )
        assert music.vibe == ("", "")


class TestAutoTrackName:
    """TrackGenerator.auto_track_name derives vibe-style-YYYYMMDD-HHMM patterns."""

    def _tg(self) -> TrackGenerator:
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


class TestMusicSchedulerTrackName:
    """MusicScheduler.track_name defaults to empty string."""

    def test_default(self) -> None:
        scheduler = MusicScheduler(TrackGenerator(Path("/tmp/vox-test-music")))
        assert scheduler.track_name == ""

    def test_music_replay_default(self) -> None:
        scheduler = MusicScheduler(TrackGenerator(Path("/tmp/vox-test-music")))
        assert scheduler.replay is False


class TestHandleMusicOnWithName:
    """MusicHandlers.handle_music_on with name field for track naming and replay."""

    def test_replay_existing_track(self, tmp_path: Path) -> None:
        """When name matches an existing file, replay without generation."""
        music_dir = tmp_path / "music"
        music_dir.mkdir()
        track = music_dir / "my_focus.mp3"
        track.write_bytes(b"fake-music")

        tg = TrackGenerator(music_dir)
        music, handlers = _make_music_handlers_and_scheduler(track_generator=tg)

        ws = AsyncMock()
        msg: dict[str, object] = {
            "type": "music_on",
            "id": "req-name-1",
            "owner_id": "session-x",
            "name": "my focus",
        }

        asyncio.run(handlers.handle_music_on(msg, ws))

        assert music.mode == "on"
        assert music.track == track
        assert music.track_name == "my_focus"
        assert music.state == "playing"
        assert music.replay is True

        resp = ws.send_json.call_args[0][0]
        assert resp["status"] == "playing"
        assert resp["name"] == "my_focus"
        assert str(track) in resp["track"]

    def test_name_not_found_generates(self, tmp_path: Path) -> None:
        """When name does not match existing file, proceed to generation."""
        music_dir = tmp_path / "music"
        music_dir.mkdir()

        tg = TrackGenerator(music_dir)
        music, handlers = _make_music_handlers_and_scheduler(track_generator=tg)

        ws = AsyncMock()
        msg: dict[str, object] = {
            "type": "music_on",
            "id": "req-name-2",
            "owner_id": "session-y",
            "name": "new track",
        }

        asyncio.run(handlers.handle_music_on(msg, ws))

        assert music.mode == "on"
        assert music.track_name == "new_track"
        assert music.state == "generating"
        assert music.changed.is_set()

        resp = ws.send_json.call_args[0][0]
        assert resp["status"] == "generating"

    def test_no_name_clears_track_name(self) -> None:
        """When no name is given, track_name is empty (auto-naming in generation)."""
        music, handlers = _make_music_handlers_and_scheduler()
        ws = AsyncMock()
        msg: dict[str, object] = {
            "type": "music_on",
            "id": "req-no-name",
            "owner_id": "session-z",
        }

        asyncio.run(handlers.handle_music_on(msg, ws))

        assert music.track_name == ""
        assert music.state == "generating"

    def test_empty_slugified_name_returns_error(self) -> None:
        """Name that slugifies to empty string returns error."""
        music, handlers = _make_music_handlers_and_scheduler()
        ws = AsyncMock()
        msg: dict[str, object] = {
            "type": "music_on",
            "id": "req-bad-name",
            "owner_id": "session-q",
            "name": "---",
        }

        asyncio.run(handlers.handle_music_on(msg, ws))

        resp = ws.send_json.call_args[0][0]
        assert resp["type"] == "error"
        assert "invalid track name" in resp["message"]
        assert music.mode == "off"


class TestHandleMusicPlay:
    """MusicHandlers.handle_music_play: replay saved tracks by name."""

    def test_play_existing_track(self, tmp_path: Path) -> None:
        music_dir = tmp_path / "music"
        music_dir.mkdir()
        track = music_dir / "chill_vibes.mp3"
        track.write_bytes(b"fake-music")

        tg = TrackGenerator(music_dir)
        music, handlers = _make_music_handlers_and_scheduler(track_generator=tg)

        ws = AsyncMock()
        msg: dict[str, object] = {
            "type": "music_play",
            "id": "play-1",
            "name": "chill vibes",
            "owner_id": "session-a",
        }

        asyncio.run(handlers.handle_music_play(msg, ws))

        assert music.mode == "on"
        assert music.track == track
        assert music.track_name == "chill_vibes"
        assert music.state == "playing"
        assert music.replay is True

        resp = ws.send_json.call_args[0][0]
        assert resp["type"] == "music_play"
        assert resp["status"] == "playing"
        assert resp["name"] == "chill_vibes"

    def test_play_not_found(self, tmp_path: Path) -> None:
        music_dir = tmp_path / "music"
        music_dir.mkdir()

        tg = TrackGenerator(music_dir)
        _music, handlers = _make_music_handlers_and_scheduler(track_generator=tg)

        ws = AsyncMock()
        msg: dict[str, object] = {
            "type": "music_play",
            "id": "play-2",
            "name": "nonexistent",
            "owner_id": "session-b",
        }

        asyncio.run(handlers.handle_music_play(msg, ws))

        resp = ws.send_json.call_args[0][0]
        assert resp["type"] == "error"
        assert "not found" in resp["message"]

    def test_play_missing_name(self) -> None:
        _music, handlers = _make_music_handlers_and_scheduler()
        ws = AsyncMock()
        msg: dict[str, object] = {
            "type": "music_play",
            "id": "play-3",
            "owner_id": "session-c",
        }

        asyncio.run(handlers.handle_music_play(msg, ws))

        resp = ws.send_json.call_args[0][0]
        assert resp["type"] == "error"
        assert "name is required" in resp["message"]

    def test_play_missing_owner_id(self) -> None:
        _music, handlers = _make_music_handlers_and_scheduler()
        ws = AsyncMock()
        msg: dict[str, object] = {
            "type": "music_play",
            "id": "play-4",
            "name": "test",
        }

        asyncio.run(handlers.handle_music_play(msg, ws))

        resp = ws.send_json.call_args[0][0]
        assert resp["type"] == "error"
        assert "owner_id is required" in resp["message"]

    def test_empty_slugified_name_returns_error(self) -> None:
        """Name that slugifies to empty string returns error."""
        _music, handlers = _make_music_handlers_and_scheduler()
        ws = AsyncMock()
        msg: dict[str, object] = {
            "type": "music_play",
            "id": "play-bad",
            "name": "---",
            "owner_id": "session-q",
        }

        asyncio.run(handlers.handle_music_play(msg, ws))

        resp = ws.send_json.call_args[0][0]
        assert resp["type"] == "error"
        assert "invalid track name" in resp["message"]


class TestHandleMusicList:
    """MusicHandlers.handle_music_list: returns saved tracks with metadata."""

    def test_list_empty_dir(self, tmp_path: Path) -> None:
        music_dir = tmp_path / "music"
        music_dir.mkdir()

        tg = TrackGenerator(music_dir)
        _music, handlers = _make_music_handlers_and_scheduler(track_generator=tg)

        ws = AsyncMock()
        msg: dict[str, object] = {"type": "music_list", "id": "list-1"}

        asyncio.run(handlers.handle_music_list(msg, ws))

        resp = ws.send_json.call_args[0][0]
        assert resp["type"] == "music_list"
        assert resp["tracks"] == []

    def test_list_with_tracks(self, tmp_path: Path) -> None:
        music_dir = tmp_path / "music"
        music_dir.mkdir()
        (music_dir / "alpha.mp3").write_bytes(b"a" * 1024)
        (music_dir / "beta.mp3").write_bytes(b"b" * 2048)

        tg = TrackGenerator(music_dir)
        _music, handlers = _make_music_handlers_and_scheduler(track_generator=tg)

        ws = AsyncMock()
        msg: dict[str, object] = {"type": "music_list", "id": "list-2"}

        asyncio.run(handlers.handle_music_list(msg, ws))

        resp = ws.send_json.call_args[0][0]
        assert resp["type"] == "music_list"
        assert len(resp["tracks"]) == 2
        names = [t["name"] for t in resp["tracks"]]
        assert "alpha" in names
        assert "beta" in names
        for t in resp["tracks"]:
            assert "size_bytes" in t
            assert "modified" in t
            assert "path" in t

    def test_list_nonexistent_dir(self, tmp_path: Path) -> None:
        music_dir = tmp_path / "music_missing"

        tg = TrackGenerator(music_dir)
        _music, handlers = _make_music_handlers_and_scheduler(track_generator=tg)

        ws = AsyncMock()
        msg: dict[str, object] = {"type": "music_list", "id": "list-3"}

        asyncio.run(handlers.handle_music_list(msg, ws))

        resp = ws.send_json.call_args[0][0]
        assert resp["type"] == "music_list"
        assert resp["tracks"] == []
