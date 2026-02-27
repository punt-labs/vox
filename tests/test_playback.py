"""Tests for punt_tts.playback."""

from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from punt_tts.playback import AFPLAY_TIMEOUT, enqueue, play_audio

_MOD = "punt_tts.playback"


class TestPlayAudio:
    def test_acquires_lock_and_calls_afplay(self, tmp_path: Path) -> None:
        audio = tmp_path / "test.mp3"
        audio.write_bytes(b"fake")

        with (
            patch(f"{_MOD}.subprocess.run") as mock_run,
            patch(f"{_MOD}.LOCK_FILE", tmp_path / "playback.lock"),
        ):
            play_audio(audio)

        mock_run.assert_called_once_with(
            ["afplay", str(audio)],
            check=False,
            timeout=AFPLAY_TIMEOUT,
        )

    def test_creates_lock_parent_directory(self, tmp_path: Path) -> None:
        lock_file = tmp_path / "nested" / "dir" / "playback.lock"
        audio = tmp_path / "test.mp3"
        audio.write_bytes(b"fake")

        with patch(f"{_MOD}.subprocess.run"), patch(f"{_MOD}.LOCK_FILE", lock_file):
            play_audio(audio)

        assert lock_file.parent.exists()

    def test_handles_missing_afplay(self, tmp_path: Path) -> None:
        audio = tmp_path / "test.mp3"
        audio.write_bytes(b"fake")

        with (
            patch(f"{_MOD}.subprocess.run", side_effect=FileNotFoundError),
            patch(f"{_MOD}.LOCK_FILE", tmp_path / "playback.lock"),
        ):
            play_audio(audio)  # should not raise

    def test_handles_timeout(self, tmp_path: Path) -> None:
        audio = tmp_path / "test.mp3"
        audio.write_bytes(b"fake")

        with (
            patch(
                f"{_MOD}.subprocess.run",
                side_effect=subprocess.TimeoutExpired("afplay", AFPLAY_TIMEOUT),
            ),
            patch(f"{_MOD}.LOCK_FILE", tmp_path / "playback.lock"),
        ):
            play_audio(audio)  # should not raise

    def test_concurrent_calls_serialize(self, tmp_path: Path) -> None:
        """Two concurrent play_audio calls should not overlap."""
        lock_file = tmp_path / "playback.lock"
        audio = tmp_path / "test.mp3"
        audio.write_bytes(b"fake")

        order: list[str] = []

        def fake_afplay(*args: object, **kwargs: object) -> None:
            order.append("start")
            time.sleep(0.05)
            order.append("end")

        with (
            patch(f"{_MOD}.subprocess.run", side_effect=fake_afplay),
            patch(f"{_MOD}.LOCK_FILE", lock_file),
        ):
            t1 = threading.Thread(target=play_audio, args=(audio,))
            t2 = threading.Thread(target=play_audio, args=(audio,))
            t1.start()
            t2.start()
            t1.join()
            t2.join()

        # Both threads ran, and the pattern must be start-end-start-end
        # (serialized), not start-start-end-end (overlapping).
        assert order == ["start", "end", "start", "end"]


class TestEnqueue:
    def test_spawns_detached_subprocess(self, tmp_path: Path) -> None:
        audio = tmp_path / "test.mp3"
        audio.write_bytes(b"fake")

        mock_popen = MagicMock()
        with patch(f"{_MOD}.subprocess.Popen", return_value=mock_popen) as popen_cls:
            enqueue(audio)

        call_args = popen_cls.call_args
        assert call_args is not None
        args_list = call_args[0][0]
        assert args_list[-1] == str(audio)
        assert call_args[1]["start_new_session"] is True

    def test_handles_spawn_failure(self, tmp_path: Path) -> None:
        audio = tmp_path / "test.mp3"
        audio.write_bytes(b"fake")

        with patch(f"{_MOD}.subprocess.Popen", side_effect=OSError("spawn failed")):
            enqueue(audio)  # should not raise


class TestCliPlay:
    """Test the `tts play` CLI command."""

    def test_play_calls_play_audio(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from punt_tts.cli import main

        audio = tmp_path / "test.mp3"
        audio.write_bytes(b"fake")

        with (
            patch(f"{_MOD}.subprocess.run") as mock_run,
            patch(f"{_MOD}.LOCK_FILE", tmp_path / "playback.lock"),
        ):
            runner = CliRunner()
            result = runner.invoke(main, ["play", str(audio)])

        assert result.exit_code == 0
        mock_run.assert_called_once()

    @pytest.mark.parametrize("exists", [False])
    def test_play_rejects_missing_file(self, tmp_path: Path, exists: bool) -> None:
        from click.testing import CliRunner

        from punt_tts.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["play", str(tmp_path / "nonexistent.mp3")])

        assert result.exit_code != 0
