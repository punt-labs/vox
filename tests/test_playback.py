"""Tests for punt_tts.playback."""

from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

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
        """Two concurrent play_audio calls should not overlap.

        Uses an Event to ensure thread 2 is started and contending
        for the lock while thread 1 holds it, preventing false
        positives from scheduler timing.
        """
        lock_file = tmp_path / "playback.lock"
        audio = tmp_path / "test.mp3"
        audio.write_bytes(b"fake")

        order: list[str] = []
        t1_holding_lock = threading.Event()

        def fake_afplay(*args: object, **kwargs: object) -> None:
            order.append("start")
            t1_holding_lock.set()
            time.sleep(0.1)
            order.append("end")

        with (
            patch(f"{_MOD}.subprocess.run", side_effect=fake_afplay),
            patch(f"{_MOD}.LOCK_FILE", lock_file),
        ):
            t1 = threading.Thread(target=play_audio, args=(audio,))
            t2 = threading.Thread(target=play_audio, args=(audio,))
            t1.start()
            t1_holding_lock.wait()  # t1 is inside fake_afplay (lock held)
            t2.start()  # t2 will block on flock until t1 releases
            t1.join()
            t2.join()

        # Serialized: start-end-start-end, not overlapping start-start-end-end.
        assert order == ["start", "end", "start", "end"]


class TestEnqueue:
    def test_spawns_detached_subprocess(self, tmp_path: Path) -> None:
        audio = tmp_path / "test.mp3"
        audio.write_bytes(b"fake")
        pending_dir = tmp_path / "pending"

        mock_popen = MagicMock()
        with (
            patch(f"{_MOD}.subprocess.Popen", return_value=mock_popen) as popen_cls,
            patch(f"{_MOD}._PENDING_DIR", pending_dir),
        ):
            enqueue(audio)

        call_args = popen_cls.call_args
        assert call_args is not None
        assert call_args[1]["start_new_session"] is True
        # Verify the pending copy was created and passed to subprocess
        assert pending_dir.exists()

    def test_handles_spawn_failure(self, tmp_path: Path) -> None:
        audio = tmp_path / "test.mp3"
        audio.write_bytes(b"fake")
        pending_dir = tmp_path / "pending"

        with (
            patch(f"{_MOD}.subprocess.Popen", side_effect=OSError("spawn failed")),
            patch(f"{_MOD}._PENDING_DIR", pending_dir),
        ):
            enqueue(audio)  # should not raise

    def test_copies_file_to_pending_dir(self, tmp_path: Path) -> None:
        """Enqueue copies the file so the original can be safely deleted."""
        audio = tmp_path / "test.mp3"
        audio.write_bytes(b"fake-audio-data")
        pending_dir = tmp_path / "pending"

        mock_popen = MagicMock()
        with (
            patch(f"{_MOD}.subprocess.Popen", return_value=mock_popen),
            patch(f"{_MOD}._PENDING_DIR", pending_dir),
        ):
            enqueue(audio)

        pending_files = list(pending_dir.iterdir())
        assert len(pending_files) == 1
        assert pending_files[0].read_bytes() == b"fake-audio-data"


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

    def test_play_rejects_missing_file(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from punt_tts.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["play", str(tmp_path / "nonexistent.mp3")])

        assert result.exit_code != 0
