"""Tests for punt_vox.voxd.fetch_handler -- single-frame store retrieval."""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from punt_vox.types_audio import FETCH_FRAME_LIMIT_BYTES
from punt_vox.voxd.fetch_handler import FetchHandler
from punt_vox.voxd.record_store import RecordStore


def _capturing_ws() -> tuple[MagicMock, list[dict[str, object]]]:
    sent: list[dict[str, object]] = []

    async def _send(payload: dict[str, object]) -> None:
        sent.append(payload)

    ws = MagicMock()
    ws.send_json = AsyncMock(side_effect=_send)
    return ws, sent


class TestFetchHandler:
    """fetch returns a store recording's bytes, contained to the root."""

    def test_remote_fetch_delivers_bytes(self, tmp_path: Path) -> None:
        store = RecordStore(tmp_path / "recordings")
        store.root.mkdir(parents=True)
        data = b"\xff\xfb\x90\x00" * 100
        (store.root / "a1b2c3.mp3").write_bytes(data)
        ws, sent = _capturing_ws()

        msg: dict[str, object] = {"type": "fetch", "id": "f1", "ref": "a1b2c3.mp3"}
        asyncio.run(FetchHandler(store=store)(msg, ws))

        reply = sent[-1]
        assert reply["type"] == "bytes"
        assert reply["bytes"] == len(data)
        assert base64.b64decode(str(reply["data"])) == data

    def test_fetch_ref_outside_root_rejected(self, tmp_path: Path) -> None:
        store = RecordStore(tmp_path / "recordings")
        ws, sent = _capturing_ws()

        msg: dict[str, object] = {"type": "fetch", "id": "f1", "ref": "/etc/passwd"}
        asyncio.run(FetchHandler(store=store)(msg, ws))

        assert sent[-1]["type"] == "error"
        assert "absolute" in str(sent[-1]["message"])

    def test_fetch_traversal_ref_rejected(self, tmp_path: Path) -> None:
        store = RecordStore(tmp_path / "recordings")
        ws, sent = _capturing_ws()

        msg: dict[str, object] = {"type": "fetch", "id": "f1", "ref": "../../etc/x"}
        asyncio.run(FetchHandler(store=store)(msg, ws))

        assert sent[-1]["type"] == "error"

    def test_unknown_recording_is_an_error(self, tmp_path: Path) -> None:
        store = RecordStore(tmp_path / "recordings")
        store.root.mkdir(parents=True)
        ws, sent = _capturing_ws()

        msg: dict[str, object] = {"type": "fetch", "id": "f1", "ref": "nope.mp3"}
        asyncio.run(FetchHandler(store=store)(msg, ws))

        assert sent[-1]["type"] == "error"
        assert "no recording" in str(sent[-1]["message"])

    def test_oversized_recording_refused_not_truncated(self, tmp_path: Path) -> None:
        """A recording above the single-frame budget is refused with a clear error."""
        store = RecordStore(tmp_path / "recordings")
        store.root.mkdir(parents=True)
        (store.root / "big.mp3").write_bytes(b"\x00" * (FETCH_FRAME_LIMIT_BYTES + 1))
        ws, sent = _capturing_ws()

        msg: dict[str, object] = {"type": "fetch", "id": "f1", "ref": "big.mp3"}
        asyncio.run(FetchHandler(store=store)(msg, ws))

        assert sent[-1]["type"] == "error"
        assert "too large" in str(sent[-1]["message"])
