"""Tests for punt_vox.voxd observability and direct-play dispatch."""
# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, MagicMock, patch

if TYPE_CHECKING:
    import pytest

from punt_vox.voxd import (
    DaemonContext,
    _health_payload,
    _play_audio,
)


def _make_ctx() -> DaemonContext:
    """Build a DaemonContext without touching real files or auth."""
    return DaemonContext(auth_token=None, port=0)


def _fake_proc(rc: int, stderr: bytes) -> MagicMock:
    """Build a fake asyncio subprocess returning (rc, stderr)."""
    proc = MagicMock()
    proc.returncode = rc
    proc.communicate = AsyncMock(return_value=(b"", stderr))
    proc.wait = AsyncMock(return_value=rc)
    proc.kill = MagicMock()
    return proc


class TestPlayAudioObservability:
    """``_play_audio`` must never silently discard playback failures."""

    def test_nonzero_exit_logs_error_and_records(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        audio = tmp_path / "out.mp3"
        audio.write_bytes(b"\xff\xfbfake mp3 body")
        ctx = _make_ctx()
        proc = _fake_proc(rc=1, stderr=b"some stderr")

        with (
            caplog.at_level(logging.ERROR, logger="punt_vox.voxd"),
            patch(
                "punt_vox.voxd.asyncio.create_subprocess_exec",
                AsyncMock(return_value=proc),
            ),
        ):
            asyncio.run(_play_audio(audio, ctx))

        assert "FAILED" in caplog.text
        assert "some stderr" in caplog.text
        assert ctx.last_playback is not None
        assert ctx.last_playback["rc"] == 1
        assert ctx.last_playback["stderr"] == "some stderr"

    def test_suspiciously_fast_success_logs_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        audio = tmp_path / "out.mp3"
        audio.write_bytes(b"\xff\xfbfake mp3 body")
        ctx = _make_ctx()
        proc = _fake_proc(rc=0, stderr=b"")

        ticks = iter([100.0, 100.001])

        with (
            caplog.at_level(logging.WARNING, logger="punt_vox.voxd"),
            patch(
                "punt_vox.voxd.asyncio.create_subprocess_exec",
                AsyncMock(return_value=proc),
            ),
            patch("punt_vox.voxd._monotonic", side_effect=lambda: next(ticks)),
        ):
            asyncio.run(_play_audio(audio, ctx))

        assert "SUSPICIOUS" in caplog.text
        assert ctx.last_playback is not None
        assert ctx.last_playback["rc"] == 0

    def test_binary_missing_logs_error(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        audio = tmp_path / "out.mp3"
        audio.write_bytes(b"\xff\xfbfake mp3 body")
        ctx = _make_ctx()

        with (
            caplog.at_level(logging.ERROR, logger="punt_vox.voxd"),
            patch(
                "punt_vox.voxd.asyncio.create_subprocess_exec",
                AsyncMock(side_effect=FileNotFoundError("no binary")),
            ),
        ):
            asyncio.run(_play_audio(audio, ctx))

        assert "FAILED" in caplog.text
        assert "not found" in caplog.text
        assert ctx.last_playback is not None
        assert ctx.last_playback["rc"] == -1
        stderr_value = cast("str", ctx.last_playback["stderr"])
        assert "FileNotFoundError" in stderr_value

    def test_zero_byte_file_logs_error(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        audio = tmp_path / "empty.mp3"
        audio.write_bytes(b"")
        ctx = _make_ctx()

        with caplog.at_level(logging.ERROR, logger="punt_vox.voxd"):
            asyncio.run(_play_audio(audio, ctx))

        assert "0-byte" in caplog.text
        assert ctx.last_playback is not None
        assert ctx.last_playback["rc"] == -1

    def test_last_playback_updated_on_success(self, tmp_path: Path) -> None:
        audio = tmp_path / "out.mp3"
        audio.write_bytes(b"\xff\xfbfake mp3 body")
        ctx = _make_ctx()
        proc = _fake_proc(rc=0, stderr=b"Stream #0:0 mp3, 44100 Hz")

        ticks = iter([100.0, 100.5])

        with (
            patch(
                "punt_vox.voxd.asyncio.create_subprocess_exec",
                AsyncMock(return_value=proc),
            ),
            patch("punt_vox.voxd._monotonic", side_effect=lambda: next(ticks)),
        ):
            asyncio.run(_play_audio(audio, ctx))

        assert ctx.last_playback is not None
        assert ctx.last_playback["rc"] == 0
        assert ctx.last_playback["elapsed_s"] == 0.5
        assert ctx.last_playback["file"] == str(audio)


class TestHealthPayload:
    """Health payload must expose audio state for vox doctor."""

    def test_includes_audio_env_and_player_binary(self) -> None:
        ctx = _make_ctx()
        payload = _health_payload(ctx)

        assert "audio_env" in payload
        assert "player_binary" in payload
        assert "last_playback" in payload
        audio_env = cast("dict[str, str]", payload["audio_env"])
        assert "XDG_RUNTIME_DIR" in audio_env
        assert "PULSE_SERVER" in audio_env
        assert "DBUS_SESSION_BUS_ADDRESS" in audio_env

    def test_last_playback_reflects_context(self, tmp_path: Path) -> None:
        ctx = _make_ctx()
        ctx.last_playback = {
            "file": str(tmp_path / "x.mp3"),
            "rc": 0,
            "elapsed_s": 1.23,
            "stderr": "",
            "ts": 0.0,
        }
        payload = _health_payload(ctx)
        assert payload["last_playback"] == ctx.last_playback
