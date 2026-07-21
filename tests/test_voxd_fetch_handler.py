"""Tests for punt_vox.voxd.fetch_handler -- single-frame store retrieval."""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

from punt_vox.types_audio import FETCH_FRAME_LIMIT_BYTES
from punt_vox.voxd.fetch_handler import FetchHandler
from punt_vox.voxd.record_store import RecordStore

if TYPE_CHECKING:
    import pytest


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

    def test_grown_between_stat_and_read_rejected_post_read(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A file that grows past the limit after the stat is rejected post-read.

        The authoritative size is the bytes actually read, not the pre-read
        stat, so a concurrent record place() cannot slip an oversize payload
        past the frame limit.
        """
        store = RecordStore(tmp_path / "recordings")
        store.root.mkdir(parents=True)
        (store.root / "x.mp3").write_bytes(b"\x00" * 10)  # small on disk (passes stat)
        ws, sent = _capturing_ws()

        def grown(_self: Path) -> bytes:
            return b"\x00" * (FETCH_FRAME_LIMIT_BYTES + 1)

        monkeypatch.setattr(Path, "read_bytes", grown)

        msg: dict[str, object] = {"type": "fetch", "id": "f1", "ref": "x.mp3"}
        asyncio.run(FetchHandler(store=store)(msg, ws))

        assert sent[-1]["type"] == "error"
        assert "too large" in str(sent[-1]["message"])

    def test_declared_bytes_match_payload(self, tmp_path: Path) -> None:
        """The reply's declared 'bytes' equals the decoded payload length."""
        store = RecordStore(tmp_path / "recordings")
        store.root.mkdir(parents=True)
        data = b"\xff\xfb\x90\x00" * 7
        (store.root / "x.mp3").write_bytes(data)
        ws, sent = _capturing_ws()

        msg: dict[str, object] = {"type": "fetch", "id": "f1", "ref": "x.mp3"}
        asyncio.run(FetchHandler(store=store)(msg, ws))

        reply = sent[-1]
        assert reply["type"] == "bytes"
        assert reply["bytes"] == len(base64.b64decode(str(reply["data"]))) == len(data)

    def test_read_error_is_a_clean_error_frame(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A ref that passes containment but errors on read → error frame, no traceback.

        Simulates the race where the file is deleted (or becomes unreadable)
        between is_file() and read_bytes(): the client must get a clean error,
        not a server-side exception.
        """
        store = RecordStore(tmp_path / "recordings")
        store.root.mkdir(parents=True)
        (store.root / "x.mp3").write_bytes(b"\xff\xfb\x90\x00" * 4)
        ws, sent = _capturing_ws()

        def boom(_self: Path) -> bytes:
            raise OSError("vanished mid-read")

        monkeypatch.setattr(Path, "read_bytes", boom)

        msg: dict[str, object] = {"type": "fetch", "id": "f1", "ref": "x.mp3"}
        asyncio.run(FetchHandler(store=store)(msg, ws))

        assert sent[-1]["type"] == "error"
        assert "cannot read recording" in str(sent[-1]["message"])
