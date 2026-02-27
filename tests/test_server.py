"""Tests for punt_tts.server playback PID management."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from punt_tts.server import (
    _kill_previous_playback,  # pyright: ignore[reportPrivateUsage]
    _play_audio,  # pyright: ignore[reportPrivateUsage]
    _record_playback_pid,  # pyright: ignore[reportPrivateUsage]
)


class TestKillPreviousPlayback:
    """Tests for _kill_previous_playback()."""

    def test_sends_sigterm_to_recorded_pid(self, tmp_path: Path) -> None:
        pid_file = tmp_path / "playback.pid"
        pid_file.write_text("12345")
        with (
            patch("punt_tts.server._PLAYBACK_PID_FILE", pid_file),
            patch("punt_tts.server.os.kill") as mock_kill,
        ):
            _kill_previous_playback()
            mock_kill.assert_called_once_with(12345, __import__("signal").SIGTERM)

    def test_no_error_when_pid_file_missing(self, tmp_path: Path) -> None:
        pid_file = tmp_path / "nonexistent" / "playback.pid"
        with patch("punt_tts.server._PLAYBACK_PID_FILE", pid_file):
            _kill_previous_playback()  # should not raise

    def test_no_error_when_pid_file_empty(self, tmp_path: Path) -> None:
        pid_file = tmp_path / "playback.pid"
        pid_file.write_text("")
        with patch("punt_tts.server._PLAYBACK_PID_FILE", pid_file):
            _kill_previous_playback()  # ValueError from int("") is caught

    def test_no_error_when_process_already_dead(self, tmp_path: Path) -> None:
        pid_file = tmp_path / "playback.pid"
        pid_file.write_text("99999")
        with (
            patch("punt_tts.server._PLAYBACK_PID_FILE", pid_file),
            patch("punt_tts.server.os.kill", side_effect=ProcessLookupError),
        ):
            _kill_previous_playback()  # should not raise


class TestRecordPlaybackPid:
    """Tests for _record_playback_pid()."""

    def test_writes_pid_to_file(self, tmp_path: Path) -> None:
        pid_file = tmp_path / "subdir" / "playback.pid"
        with patch("punt_tts.server._PLAYBACK_PID_FILE", pid_file):
            _record_playback_pid(42)
            assert pid_file.read_text() == "42"

    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        pid_file = tmp_path / "deep" / "nested" / "playback.pid"
        with patch("punt_tts.server._PLAYBACK_PID_FILE", pid_file):
            _record_playback_pid(100)
            assert pid_file.parent.is_dir()
            assert pid_file.read_text() == "100"


class TestPlayAudio:
    """Tests for _play_audio() integration with PID management."""

    def test_kills_previous_before_spawning(self, tmp_path: Path) -> None:
        pid_file = tmp_path / "playback.pid"
        pid_file.write_text("11111")
        mock_proc = MagicMock()
        mock_proc.pid = 22222
        with (
            patch("punt_tts.server._PLAYBACK_PID_FILE", pid_file),
            patch("punt_tts.server.os.kill") as mock_kill,
            patch("punt_tts.server.subprocess.Popen", return_value=mock_proc),
        ):
            _play_audio(Path("/fake/audio.mp3"))
            mock_kill.assert_called_once_with(11111, __import__("signal").SIGTERM)
            assert pid_file.read_text() == "22222"

    def test_records_new_pid_after_spawn(self, tmp_path: Path) -> None:
        pid_file = tmp_path / "playback.pid"
        mock_proc = MagicMock()
        mock_proc.pid = 55555
        with (
            patch("punt_tts.server._PLAYBACK_PID_FILE", pid_file),
            patch("punt_tts.server.subprocess.Popen", return_value=mock_proc),
        ):
            _play_audio(Path("/fake/audio.mp3"))
            assert pid_file.read_text() == "55555"

    def test_handles_missing_afplay(self, tmp_path: Path) -> None:
        pid_file = tmp_path / "playback.pid"
        with (
            patch("punt_tts.server._PLAYBACK_PID_FILE", pid_file),
            patch(
                "punt_tts.server.subprocess.Popen",
                side_effect=FileNotFoundError,
            ),
        ):
            _play_audio(Path("/fake/audio.mp3"))  # should not raise
            assert not pid_file.exists()  # no PID recorded on failure
