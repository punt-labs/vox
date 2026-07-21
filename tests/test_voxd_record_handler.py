"""Tests for punt_vox.voxd.record_handler -- daemon-side record file writing."""
# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.websockets import WebSocketDisconnect

from punt_vox.voxd.record_handler import RecordHandler
from punt_vox.voxd.synthesis_result import SynthesisOutcome


def _record_handler_with_source(source: Path, *, cached: bool = False) -> RecordHandler:
    """Build a RecordHandler whose synthesis lands *source* (no real TTS)."""
    synth = MagicMock()
    synth.synthesize_to_file = AsyncMock(
        return_value=SynthesisOutcome(path=source, cached=cached)
    )
    return RecordHandler(synthesis=synth)


def _capturing_ws() -> tuple[MagicMock, list[dict[str, object]]]:
    """Return a mock websocket and the list of payloads it is sent."""
    sent: list[dict[str, object]] = []

    async def _send(payload: dict[str, object]) -> None:
        sent.append(payload)

    ws = MagicMock()
    ws.send_json = AsyncMock(side_effect=_send)
    return ws, sent


class TestRecordHandler:
    """The record handler writes the file daemon-side and returns its path."""

    def test_long_record_delivers_byte_correct_file(self, tmp_path: Path) -> None:
        data = b"\xff\xfb\x90\x00" * 300_000  # ~1.2 MiB, over the old 1 MiB cap
        src = tmp_path / "src.mp3"
        src.write_bytes(data)
        handler = _record_handler_with_source(src)
        ws, sent = _capturing_ws()
        out_dir = tmp_path / "out"

        msg: dict[str, object] = {
            "type": "record",
            "id": "r1",
            "text": "hello",
            "output_dir": str(out_dir),
        }
        asyncio.run(handler(msg, ws))

        assert sent[0]["type"] == "recording"  # ack precedes the file
        audio = next(p for p in sent if p["type"] == "audio")
        landed = Path(str(audio["path"]))
        assert landed.read_bytes() == data
        assert audio["bytes"] == len(data) == landed.stat().st_size

    def test_record_over_frame_limit_succeeds(self, tmp_path: Path) -> None:
        data = b"\x00" * (2 * 1024 * 1024)  # 2 MiB
        src = tmp_path / "src.mp3"
        src.write_bytes(data)
        handler = _record_handler_with_source(src)
        ws, sent = _capturing_ws()

        msg: dict[str, object] = {
            "type": "record",
            "id": "r1",
            "text": "big",
            "output_dir": str(tmp_path / "out"),
        }
        asyncio.run(handler(msg, ws))

        audio = next(p for p in sent if p["type"] == "audio")
        assert "path" in audio
        assert "data" not in audio  # no base64 audio frame crosses the wire

    @pytest.mark.parametrize("mib", [1, 3, 5])
    def test_arbitrarily_long_record_has_no_ceiling(
        self, tmp_path: Path, mib: int
    ) -> None:
        data = b"\x01" * (mib * 1024 * 1024)
        src = tmp_path / "src.mp3"
        src.write_bytes(data)
        handler = _record_handler_with_source(src)
        ws, sent = _capturing_ws()

        msg: dict[str, object] = {
            "type": "record",
            "id": "r1",
            "text": f"len{mib}",
            "output_dir": str(tmp_path / "out"),
        }
        asyncio.run(handler(msg, ws))

        audio = next(p for p in sent if p["type"] == "audio")
        assert audio["bytes"] == mib * 1024 * 1024

    def test_client_close_mid_transfer_does_not_crash_daemon(
        self, tmp_path: Path
    ) -> None:
        src = tmp_path / "src.mp3"
        src.write_bytes(b"\xff\xfb\x90\x00" * 10)
        handler = _record_handler_with_source(src)
        # Ack succeeds; the terminal 'audio' reply hits a vanished client.
        ws = MagicMock()
        ws.send_json = AsyncMock(side_effect=[None, WebSocketDisconnect()])

        msg: dict[str, object] = {
            "type": "record",
            "id": "r1",
            "text": "hi",
            "output_dir": str(tmp_path / "out"),
        }
        # Must not raise -- a client disconnect is a normal end-of-request.
        asyncio.run(handler(msg, ws))
        assert ws.send_json.await_count == 2

    def test_no_partial_file_on_synthesis_error(self, tmp_path: Path) -> None:
        synth = MagicMock()
        synth.synthesize_to_file = AsyncMock(side_effect=RuntimeError("boom"))
        handler = RecordHandler(synthesis=synth)
        ws, sent = _capturing_ws()
        out_dir = tmp_path / "out"

        msg: dict[str, object] = {
            "type": "record",
            "id": "r1",
            "text": "hi",
            "output_dir": str(out_dir),
        }
        asyncio.run(handler(msg, ws))

        error = next(p for p in sent if p["type"] == "error")
        assert "boom" in str(error["message"])
        assert not out_dir.exists() or not any(out_dir.iterdir())

    def test_missing_output_dir_is_an_error(self, tmp_path: Path) -> None:
        src = tmp_path / "src.mp3"
        src.write_bytes(b"\x00")
        handler = _record_handler_with_source(src)
        ws, sent = _capturing_ws()

        msg: dict[str, object] = {"type": "record", "id": "r1", "text": "hi"}
        asyncio.run(handler(msg, ws))

        assert sent[-1]["type"] == "error"
        assert "output_dir" in str(sent[-1]["message"])
