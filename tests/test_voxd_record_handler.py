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

    def test_client_close_mid_transfer_daemon_serves_next_connection(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """After a client vanishes mid-record, a NEW connection still works."""
        from starlette.testclient import TestClient

        from punt_vox.voxd import build_app
        from punt_vox.voxd.synthesis import SynthesisPipeline

        async def fake_synth(
            _self: SynthesisPipeline, _text: str, _spec: object
        ) -> SynthesisOutcome:
            src = tmp_path / "synth.mp3"
            src.write_bytes(b"\xff\xfb\x90\x00" * 8)
            return SynthesisOutcome(path=src, cached=False)

        monkeypatch.setattr(SynthesisPipeline, "synthesize_to_file", fake_synth)

        app = build_app()
        with TestClient(app) as client:
            with client.websocket_connect("/ws") as ws1:
                ws1.send_json(
                    {
                        "type": "record",
                        "id": "r1",
                        "text": "hi",
                        "output_dir": str(tmp_path / "out"),
                    }
                )
                assert ws1.receive_json()["type"] == "recording"
                # Exit the context = close mid-transfer, before the 'audio' frame.

            with client.websocket_connect("/ws") as ws2:
                ws2.send_json({"type": "health"})
                assert ws2.receive_json()["type"] == "health"

    def test_client_gone_before_ack_skips_synthesis(self, tmp_path: Path) -> None:
        """A client gone before the ack skips synthesis and writes no file."""
        synth = MagicMock()
        synth.synthesize_to_file = AsyncMock(
            return_value=SynthesisOutcome(path=tmp_path / "x.mp3", cached=False)
        )
        handler = RecordHandler(synthesis=synth)
        ws = MagicMock()
        # The ack itself hits a vanished client.
        ws.send_json = AsyncMock(side_effect=WebSocketDisconnect())
        out_dir = tmp_path / "out"

        msg: dict[str, object] = {
            "type": "record",
            "id": "r1",
            "text": "hi",
            "output_dir": str(out_dir),
        }
        asyncio.run(handler(msg, ws))

        synth.synthesize_to_file.assert_not_called()
        assert not out_dir.exists()

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

    def test_malformed_output_dir_is_a_clean_error(self, tmp_path: Path) -> None:
        """A malformed path (embedded NUL) yields an error frame, not a crash."""
        src = tmp_path / "src.mp3"
        src.write_bytes(b"\x00")
        handler = _record_handler_with_source(src)
        ws, sent = _capturing_ws()

        msg: dict[str, object] = {
            "type": "record",
            "id": "r1",
            "text": "hi",
            "output_dir": "/bad\x00dir",
        }
        # Must not raise -- a malformed path is a clean error, never a hang.
        asyncio.run(handler(msg, ws))

        assert any(p["type"] == "error" for p in sent)
        assert not any(p["type"] == "audio" for p in sent)

    def test_invalid_path_parse_is_rejected_before_ack(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A ValueError from path parsing is a clean error sent before the ack."""
        src = tmp_path / "src.mp3"
        src.write_bytes(b"\x00")
        handler = _record_handler_with_source(src)
        ws, sent = _capturing_ws()

        def boom(_self: Path) -> bool:
            raise ValueError("bad path")

        monkeypatch.setattr(Path, "is_absolute", boom)

        msg: dict[str, object] = {
            "type": "record",
            "id": "r1",
            "text": "hi",
            "output_dir": "/some/dir",
        }
        asyncio.run(handler(msg, ws))

        assert sent[-1]["type"] == "error"
        assert "invalid output path" in str(sent[-1]["message"])
        # Rejected before the ack -- no recording/audio frame was sent.
        assert not any(p["type"] in ("recording", "audio") for p in sent)

    def test_relative_output_dir_is_rejected(self, tmp_path: Path) -> None:
        """A relative output_dir over the wire yields an error, not a write."""
        src = tmp_path / "src.mp3"
        src.write_bytes(b"\x00")
        handler = _record_handler_with_source(src)
        ws, sent = _capturing_ws()

        msg: dict[str, object] = {
            "type": "record",
            "id": "r1",
            "text": "hi",
            "output_dir": "relative/dir",
        }
        asyncio.run(handler(msg, ws))

        assert sent[-1]["type"] == "error"
        assert "absolute output_dir" in str(sent[-1]["message"])
        assert not any(p["type"] == "audio" for p in sent)

    def test_relative_output_path_is_rejected(self, tmp_path: Path) -> None:
        """A relative output_path over the wire yields an error, not a write."""
        src = tmp_path / "src.mp3"
        src.write_bytes(b"\x00")
        handler = _record_handler_with_source(src)
        ws, sent = _capturing_ws()

        msg: dict[str, object] = {
            "type": "record",
            "id": "r1",
            "text": "hi",
            "output_dir": str(tmp_path / "out"),
            "output_path": "rel.mp3",
        }
        asyncio.run(handler(msg, ws))

        assert sent[-1]["type"] == "error"
        assert "absolute output_path" in str(sent[-1]["message"])
        assert not any(p["type"] == "audio" for p in sent)
