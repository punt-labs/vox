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
        assert reply["ref"] == "a1b2c3.mp3"

    def test_reply_echoes_requested_ref_not_ondisk_name(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The reply echoes the requested ref, not the on-disk name.

        On a case-insensitive filesystem a mixed-case ref resolves to a
        differently-cased on-disk file; the reply must carry the ref the client
        asked for so its exact-match check holds after a successful read. Stub
        resolve_ref so the on-disk name provably differs from the requested ref
        (deterministic on any filesystem).
        """
        store = RecordStore(tmp_path / "recordings")
        store.root.mkdir(parents=True)
        ondisk = store.root / "actual.mp3"
        ondisk.write_bytes(b"\xff\xfb\x90\x00" * 4)

        def stub_resolve(_self: RecordStore, _ref: str) -> Path:
            return ondisk

        monkeypatch.setattr(RecordStore, "resolve_ref", stub_resolve)
        ws, sent = _capturing_ws()

        msg: dict[str, object] = {"type": "fetch", "id": "f1", "ref": "REQUESTED.mp3"}
        asyncio.run(FetchHandler(store=store)(msg, ws))

        reply = sent[-1]
        assert reply["type"] == "bytes"
        assert reply["ref"] == "REQUESTED.mp3"  # the requested ref, not "actual.mp3"

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

    def test_grown_between_stat_and_read_is_bounded_and_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A file grown past the limit after the stat is read bounded, then rejected.

        The pre-read stat sees a small size (passes the fast-path), but the file
        has grown; the handler reads at most FETCH_FRAME_LIMIT_BYTES + 1, so the
        worst-case allocation is bounded (no memory/DoS), and len > limit rejects
        it as oversize -- a token-holding remote caller can't drive an
        arbitrarily large allocation via a concurrent record place().
        """
        store = RecordStore(tmp_path / "recordings")
        store.root.mkdir(parents=True)
        target = store.root / "x.mp3"
        target.write_bytes(b"\x00" * 10)  # small on disk -> pre-read stat passes
        ws, sent = _capturing_ws()

        class _FakeHandle:
            def __init__(self) -> None:
                self.read_arg: int | None = None

            def read(self, size: int = -1) -> bytes:
                self.read_arg = size  # a huge file returns exactly what is asked
                return b"\x00" * size

            def __enter__(self) -> _FakeHandle:
                return self

            def __exit__(self, *_exc: object) -> None:
                return None

        handles: list[_FakeHandle] = []

        def fake_open(self: Path, *_a: object, **_k: object) -> _FakeHandle:
            assert self == target, f"unexpected open of {self}"
            handle = _FakeHandle()
            handles.append(handle)
            return handle

        monkeypatch.setattr(Path, "open", fake_open)

        msg: dict[str, object] = {"type": "fetch", "id": "f1", "ref": "x.mp3"}
        asyncio.run(FetchHandler(store=store)(msg, ws))

        assert sent[-1]["type"] == "error"
        assert "too large" in str(sent[-1]["message"])
        # The read never asked for more than limit + 1 bytes (bounded allocation).
        assert handles[0].read_arg == FETCH_FRAME_LIMIT_BYTES + 1

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
        between is_file() and the open/read: the client must get a clean error,
        not a server-side exception.
        """
        store = RecordStore(tmp_path / "recordings")
        store.root.mkdir(parents=True)
        (store.root / "x.mp3").write_bytes(b"\xff\xfb\x90\x00" * 4)
        ws, sent = _capturing_ws()

        def boom(_self: Path, *_a: object, **_k: object) -> object:
            raise OSError("vanished mid-read")

        monkeypatch.setattr(Path, "open", boom)

        msg: dict[str, object] = {"type": "fetch", "id": "f1", "ref": "x.mp3"}
        asyncio.run(FetchHandler(store=store)(msg, ws))

        assert sent[-1]["type"] == "error"
        assert "cannot read recording" in str(sent[-1]["message"])

    def test_client_disconnect_on_send_does_not_raise(self, tmp_path: Path) -> None:
        """A client gone when the bytes frame is sent ends the request quietly."""
        from starlette.websockets import WebSocketDisconnect

        store = RecordStore(tmp_path / "recordings")
        store.root.mkdir(parents=True)
        (store.root / "x.mp3").write_bytes(b"\xff\xfb\x90\x00" * 4)
        ws = MagicMock()
        ws.send_json = AsyncMock(side_effect=WebSocketDisconnect())

        msg: dict[str, object] = {"type": "fetch", "id": "f1", "ref": "x.mp3"}
        # Must not raise -- a normal disconnect is a quiet end-of-request.
        asyncio.run(FetchHandler(store=store)(msg, ws))
