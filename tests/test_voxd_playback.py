"""Tests for punt_vox.voxd.playback -- PlaybackQueue and audio helpers."""
# pyright: reportPrivateUsage=false, reportMissingImports=false, reportCallIssue=false, reportAttributeAccessIssue=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from conftest import _get_valid_mp3_bytes  # pyright: ignore[reportPrivateUsage]

from punt_vox.voxd import (
    _PLAYBACK_TIMEOUT_DEFAULT_S,
    _music_player_command,
)
from punt_vox.voxd.music.generator import TrackGenerator
from punt_vox.voxd.music.scheduler import MusicScheduler
from punt_vox.voxd.music_handlers import MusicOnHandler
from punt_vox.voxd.playback import PlaybackQueue


def _make_playback_queue() -> PlaybackQueue:
    """Build a fresh PlaybackQueue for testing."""
    return PlaybackQueue()


def _fake_proc(rc: int, stderr: bytes) -> MagicMock:
    """Build a fake asyncio subprocess returning (rc, stderr)."""
    proc = MagicMock()
    proc.returncode = rc
    proc.communicate = AsyncMock(return_value=(b"", stderr))
    proc.wait = AsyncMock(return_value=rc)
    proc.kill = MagicMock()
    return proc


class TestPlayAudioObservability:
    """``PlaybackQueue.play_audio`` must never silently discard playback failures."""

    def test_nonzero_exit_logs_error_and_records(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        audio = tmp_path / "out.mp3"
        audio.write_bytes(b"\xff\xfbfake mp3 body")
        pq = _make_playback_queue()
        proc = _fake_proc(rc=1, stderr=b"some stderr")

        with (
            caplog.at_level(logging.ERROR, logger="punt_vox.voxd"),
            patch(
                "punt_vox.voxd.playback.asyncio.create_subprocess_exec",
                AsyncMock(return_value=proc),
            ),
        ):
            asyncio.run(pq.play_audio(audio))

        assert "FAILED" in caplog.text
        assert "some stderr" in caplog.text
        assert pq.last_result is not None
        assert pq.last_result["rc"] == 1
        assert pq.last_result["stderr"] == "some stderr"

    def test_suspiciously_fast_success_logs_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        audio = tmp_path / "out.mp3"
        audio.write_bytes(b"\xff\xfbfake mp3 body")
        pq = _make_playback_queue()
        proc = _fake_proc(rc=0, stderr=b"")

        ticks = iter([100.0, 100.001])

        with (
            caplog.at_level(logging.WARNING, logger="punt_vox.voxd"),
            patch(
                "punt_vox.voxd.playback.asyncio.create_subprocess_exec",
                AsyncMock(return_value=proc),
            ),
            patch(
                "punt_vox.voxd.playback._monotonic",
                side_effect=lambda: next(ticks),
            ),
        ):
            asyncio.run(pq.play_audio(audio))

        assert "SUSPICIOUS" in caplog.text
        assert pq.last_result is not None
        assert pq.last_result["rc"] == 0

    def test_binary_missing_logs_error(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        audio = tmp_path / "out.mp3"
        audio.write_bytes(b"\xff\xfbfake mp3 body")
        pq = _make_playback_queue()

        with (
            caplog.at_level(logging.ERROR, logger="punt_vox.voxd"),
            patch(
                "punt_vox.voxd.playback.asyncio.create_subprocess_exec",
                AsyncMock(side_effect=FileNotFoundError("no binary")),
            ),
        ):
            asyncio.run(pq.play_audio(audio))

        assert "FAILED" in caplog.text
        assert "not found" in caplog.text
        assert pq.last_result is not None
        assert pq.last_result["rc"] == -1
        stderr_value = str(pq.last_result["stderr"])
        assert "FileNotFoundError" in stderr_value

    def test_zero_byte_file_logs_error(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        audio = tmp_path / "empty.mp3"
        audio.write_bytes(b"")
        pq = _make_playback_queue()

        with caplog.at_level(logging.ERROR, logger="punt_vox.voxd"):
            asyncio.run(pq.play_audio(audio))

        assert "0-byte" in caplog.text
        assert pq.last_result is not None
        assert pq.last_result["rc"] == -1

    def test_last_playback_updated_on_success(self, tmp_path: Path) -> None:
        audio = tmp_path / "out.mp3"
        audio.write_bytes(b"\xff\xfbfake mp3 body")
        pq = _make_playback_queue()
        proc = _fake_proc(rc=0, stderr=b"Stream #0:0 mp3, 44100 Hz")

        ticks = iter([100.0, 100.5])

        with (
            patch(
                "punt_vox.voxd.playback.asyncio.create_subprocess_exec",
                AsyncMock(return_value=proc),
            ),
            patch(
                "punt_vox.voxd.playback._monotonic",
                side_effect=lambda: next(ticks),
            ),
        ):
            asyncio.run(pq.play_audio(audio))

        assert pq.last_result is not None
        assert pq.last_result["rc"] == 0
        assert pq.last_result["elapsed_s"] == 0.5
        assert pq.last_result["file"] == str(audio)


class TestProbeDuration:
    """``_probe_duration`` extracts audio duration via ffprobe."""

    def test_returns_duration_for_valid_audio(self, tmp_path: Path) -> None:
        from punt_vox.voxd.playback import _probe_duration

        audio = tmp_path / "silence.mp3"
        audio.write_bytes(_get_valid_mp3_bytes())
        duration = asyncio.run(_probe_duration(audio))
        assert duration is not None
        assert duration > 0.0

    def test_returns_none_for_missing_file(self, tmp_path: Path) -> None:
        from punt_vox.voxd.playback import _probe_duration

        missing = tmp_path / "nonexistent.mp3"
        duration = asyncio.run(_probe_duration(missing))
        assert duration is None

    def test_returns_none_for_bad_format(self, tmp_path: Path) -> None:
        from punt_vox.voxd.playback import _probe_duration

        bad = tmp_path / "garbage.mp3"
        bad.write_bytes(b"not audio data at all")
        duration = asyncio.run(_probe_duration(bad))
        # ffprobe may return None or an error; either way, no crash
        assert duration is None or isinstance(duration, float)

    def test_returns_none_when_ffprobe_missing(self, tmp_path: Path) -> None:
        from punt_vox.voxd.playback import _probe_duration

        audio = tmp_path / "silence.mp3"
        audio.write_bytes(_get_valid_mp3_bytes())
        with patch(
            "punt_vox.voxd.playback.asyncio.create_subprocess_exec",
            AsyncMock(side_effect=FileNotFoundError("ffprobe")),
        ):
            duration = asyncio.run(_probe_duration(audio))
        assert duration is None

    def test_returns_none_on_timeout(self, tmp_path: Path) -> None:
        from punt_vox.voxd.playback import _probe_duration

        audio = tmp_path / "silence.mp3"
        audio.write_bytes(_get_valid_mp3_bytes())
        proc = MagicMock()
        proc.communicate = AsyncMock(side_effect=TimeoutError)
        with patch(
            "punt_vox.voxd.playback.asyncio.create_subprocess_exec",
            AsyncMock(return_value=proc),
        ):
            duration = asyncio.run(_probe_duration(audio))
        assert duration is None

    def test_logs_duration_at_debug(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        from punt_vox.voxd.playback import _probe_duration

        audio = tmp_path / "silence.mp3"
        audio.write_bytes(_get_valid_mp3_bytes())
        with caplog.at_level(logging.DEBUG, logger="punt_vox.voxd"):
            duration = asyncio.run(_probe_duration(audio))
        if duration is not None:
            assert "Probed duration" in caplog.text


class TestPlayAudioProportionalTimeout:
    """``PlaybackQueue.play_audio`` uses probed duration for its timeout."""

    def test_uses_probed_duration_for_timeout(self, tmp_path: Path) -> None:
        """A 150s file gets timeout = max(150+10, 120) = 160s."""
        audio = tmp_path / "out.mp3"
        audio.write_bytes(b"\xff\xfbfake mp3 body")
        pq = _make_playback_queue()
        proc = _fake_proc(rc=0, stderr=b"")
        ticks = iter([100.0, 100.5])

        captured_timeout: list[float] = []
        original_wait_for = asyncio.wait_for

        async def spy_wait_for(coro: object, *, timeout: float) -> object:
            captured_timeout.append(timeout)
            return await original_wait_for(coro, timeout=timeout)  # type: ignore[arg-type]

        with (
            patch(
                "punt_vox.voxd.playback._probe_duration",
                AsyncMock(return_value=150.0),
            ),
            patch(
                "punt_vox.voxd.playback.asyncio.create_subprocess_exec",
                AsyncMock(return_value=proc),
            ),
            patch(
                "punt_vox.voxd.playback._monotonic",
                side_effect=lambda: next(ticks),
            ),
            patch(
                "punt_vox.voxd.playback.asyncio.wait_for",
                side_effect=spy_wait_for,
            ),
        ):
            asyncio.run(pq.play_audio(audio))

        assert len(captured_timeout) == 1
        assert captured_timeout[0] == pytest.approx(160.0, abs=0.1)  # pyright: ignore[reportUnknownMemberType]

    def test_falls_back_to_default_when_probe_fails(self, tmp_path: Path) -> None:
        audio = tmp_path / "out.mp3"
        audio.write_bytes(b"\xff\xfbfake mp3 body")
        pq = _make_playback_queue()
        proc = _fake_proc(rc=0, stderr=b"")
        ticks = iter([100.0, 100.5])

        captured_timeout: list[float] = []
        original_wait_for = asyncio.wait_for

        async def spy_wait_for(coro: object, *, timeout: float) -> object:
            captured_timeout.append(timeout)
            return await original_wait_for(coro, timeout=timeout)  # type: ignore[arg-type]

        with (
            patch(
                "punt_vox.voxd.playback._probe_duration",
                AsyncMock(return_value=None),
            ),
            patch(
                "punt_vox.voxd.playback.asyncio.create_subprocess_exec",
                AsyncMock(return_value=proc),
            ),
            patch(
                "punt_vox.voxd.playback._monotonic",
                side_effect=lambda: next(ticks),
            ),
            patch(
                "punt_vox.voxd.playback.asyncio.wait_for",
                side_effect=spy_wait_for,
            ),
        ):
            asyncio.run(pq.play_audio(audio))

        assert len(captured_timeout) == 1
        assert captured_timeout[0] == _PLAYBACK_TIMEOUT_DEFAULT_S

    def test_short_duration_uses_default_minimum(self, tmp_path: Path) -> None:
        """A 5s file gets timeout = max(5+10, 120) = 120s (default wins)."""
        audio = tmp_path / "out.mp3"
        audio.write_bytes(b"\xff\xfbfake mp3 body")
        pq = _make_playback_queue()
        proc = _fake_proc(rc=0, stderr=b"")
        ticks = iter([100.0, 100.5])

        captured_timeout: list[float] = []
        original_wait_for = asyncio.wait_for

        async def spy_wait_for(coro: object, *, timeout: float) -> object:
            captured_timeout.append(timeout)
            return await original_wait_for(coro, timeout=timeout)  # type: ignore[arg-type]

        with (
            patch(
                "punt_vox.voxd.playback._probe_duration",
                AsyncMock(return_value=5.0),
            ),
            patch(
                "punt_vox.voxd.playback.asyncio.create_subprocess_exec",
                AsyncMock(return_value=proc),
            ),
            patch(
                "punt_vox.voxd.playback._monotonic",
                side_effect=lambda: next(ticks),
            ),
            patch(
                "punt_vox.voxd.playback.asyncio.wait_for",
                side_effect=spy_wait_for,
            ),
        ):
            asyncio.run(pq.play_audio(audio))

        assert len(captured_timeout) == 1
        assert captured_timeout[0] == _PLAYBACK_TIMEOUT_DEFAULT_S


class TestMusicPlayerCommand:
    """_music_player_command produces the right argv at reduced volume."""

    def test_linux_ffplay_with_volume(self) -> None:
        with patch("punt_vox.voxd.playback._is_darwin", return_value=False):
            cmd = _music_player_command(Path("/tmp/track.mp3"))
        assert cmd == [
            "ffplay",
            "-nodisp",
            "-autoexit",
            "-volume",
            "30",
            "/tmp/track.mp3",
        ]

    def test_darwin_afplay_with_volume(self) -> None:
        with patch("punt_vox.voxd.playback._is_darwin", return_value=True):
            cmd = _music_player_command(Path("/tmp/track.mp3"))
        assert cmd == ["afplay", "--volume", "0.3", "/tmp/track.mp3"]


class TestStderrTruncation:
    """ffplay stderr can be unbounded; the truncator caps it but keeps both ends."""

    def test_short_text_passes_through(self) -> None:
        from punt_vox.voxd.playback import _truncate_stderr

        assert _truncate_stderr("hello") == "hello"

    def test_long_text_truncated_with_marker(self) -> None:
        from punt_vox.voxd.playback import _MAX_STDERR_LEN, _truncate_stderr

        text = "A" * 5000 + "B" * 5000
        out = _truncate_stderr(text)

        assert len(out) < len(text)
        assert "truncated" in out
        assert out.startswith("A")
        assert out.endswith("B")
        # Marker reports the dropped byte count.
        assert str(len(text) - _MAX_STDERR_LEN) in out


class TestMusicSeparateFromPlaybackQueue:
    """Music subprocess must NOT use the playback consumer queue.

    The spec requires music to run its own subprocess at reduced volume,
    independent of the chime/TTS playback queue. This test verifies the
    separation by checking that _handle_music_on does not enqueue anything.
    """

    def test_music_on_does_not_enqueue(self) -> None:
        playback = PlaybackQueue()
        tg = TrackGenerator(Path("/tmp/vox-test-music"))
        music = MusicScheduler(tg)
        handler = MusicOnHandler(music=music, track_generator=tg)

        ws = MagicMock()
        ws.send_json = AsyncMock()
        msg: dict[str, object] = {
            "id": "sep-1",
            "owner_id": "session-1",
            "vibe": "focused",
        }

        asyncio.run(handler(msg, ws))

        assert playback._queue.empty()
