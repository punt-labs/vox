"""Tests for the recording listing handler."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

from starlette.websockets import WebSocketDisconnect

from punt_vox.client import VoxClient
from punt_vox.voxd.rec_list_handler import RecListHandler
from punt_vox.voxd.record_store import RecordingEntry, RecordStore

if TYPE_CHECKING:
    from collections.abc import Iterator

    import pytest
    from starlette.websockets import WebSocket


class TestRecList:
    def test_large_list_streams_bounded_ordered_frames(
        self,
        recording_store: RecordStore,
        recording_socket: tuple[WebSocket, list[dict[str, object]]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        names = [f"scout-{i:06d}.mp3" for i in range(32_000)]
        snapshot = [RecordingEntry(name, i) for i, name in enumerate(names)]

        def scan(_store: RecordStore) -> Iterator[list[RecordingEntry]]:
            yield snapshot[::-1]

        monkeypatch.setattr(RecordStore, "entry_batches", scan)
        ws, sent = recording_socket

        asyncio.run(RecListHandler(recording_store)({"id": "l1"}, ws))

        assert all(len(json.dumps(frame).encode()) < 2**20 for frame in sent)
        assert len(sent) > 1
        rows: list[object] = []
        for index, frame in enumerate(sent):
            assert frame["id"] == "l1"
            assert frame["type"] == "recordings_page"
            assert frame["page"] == index
            assert frame["more"] is (index < len(sent) - 1)
            entries = frame["entries"]
            assert isinstance(entries, list)
            rows.extend(entries)
        assert rows == [{"name": name, "bytes": i} for i, name in enumerate(names)]

    async def test_client_reassembles_pages_from_a_real_store(
        self,
        recording_store: RecordStore,
        recording_socket: tuple[WebSocket, list[dict[str, object]]],
    ) -> None:
        expected = [(f"{i:04d}.mp3", i) for i in range(257)]
        for name, size in reversed(expected):
            (recording_store.root / name).write_bytes(b"x" * size)
        handler = RecListHandler(recording_store)
        websocket, sent = recording_socket

        async def send(raw: str) -> None:
            await handler(json.loads(raw), websocket)

        async def receive() -> str:
            return json.dumps(sent.pop(0))

        peer = AsyncMock()
        peer.send.side_effect = send
        peer.recv.side_effect = receive
        with patch(
            "punt_vox.client_transport.websockets.asyncio.client.connect",
            new_callable=AsyncMock,
            return_value=peer,
        ):
            rows = await VoxClient(port=8421, token="tok").rec_list()

        assert peer.recv.await_count > 1
        assert not sent
        assert [(row.name, row.byte_count) for row in rows] == expected

    async def test_stops_sending_pages_when_peer_disconnects(
        self, recording_store: RecordStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def scan(_store: RecordStore) -> Iterator[list[RecordingEntry]]:
            yield [RecordingEntry(f"{i:04d}.mp3", 0) for i in range(257)]

        monkeypatch.setattr(RecordStore, "entry_batches", scan)
        peer = AsyncMock()
        peer.send_json.side_effect = WebSocketDisconnect(1000)
        await RecListHandler(recording_store)({"id": "l1"}, peer)

        peer.send_json.assert_awaited_once()

    def test_empty_stream_has_one_terminal_page(
        self,
        recording_store: RecordStore,
        recording_socket: tuple[WebSocket, list[dict[str, object]]],
    ) -> None:
        ws, sent = recording_socket
        asyncio.run(RecListHandler(recording_store)({"id": "l1"}, ws))
        assert sent == [
            {
                "type": "recordings_page",
                "page": 0,
                "more": False,
                "entries": [],
                "id": "l1",
            }
        ]

    def test_list_scans_outside_event_loop(
        self,
        recording_store: RecordStore,
        recording_socket: tuple[WebSocket, list[dict[str, object]]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        loop_thread = threading.get_ident()
        scan_threads: list[int] = []

        def scan(_store: RecordStore) -> Iterator[list[RecordingEntry]]:
            scan_threads.append(threading.get_ident())
            yield []

        monkeypatch.setattr(RecordStore, "entry_batches", scan)
        ws, _ = recording_socket
        asyncio.run(RecListHandler(recording_store)({"id": "l1"}, ws))
        assert len(scan_threads) == 1
        assert scan_threads[0] != loop_thread

    def test_empty_store_lists_nothing(
        self,
        recording_store: RecordStore,
        recording_socket: tuple[WebSocket, list[dict[str, object]]],
    ) -> None:
        ws, sent = recording_socket
        asyncio.run(RecListHandler(recording_store)({"id": "l1"}, ws))
        assert sent[-1]["type"] == "recordings_page"
        assert sent[-1]["entries"] == []

    def test_lists_immediate_files_with_bytes(
        self,
        recording_store: RecordStore,
        recording_socket: tuple[WebSocket, list[dict[str, object]]],
    ) -> None:
        (recording_store.root / "a.mp3").write_bytes(b"12345")
        (recording_store.root / "b.mp3").write_bytes(b"12")
        ws, sent = recording_socket
        asyncio.run(RecListHandler(recording_store)({"id": "l1"}, ws))
        entries = sent[-1]["entries"]
        assert isinstance(entries, list)
        pairs = {(e["name"], e["bytes"]) for e in entries}
        assert pairs == {("a.mp3", 5), ("b.mp3", 2)}

    def test_listing_io_error_is_an_operational_fault(
        self,
        recording_store: RecordStore,
        recording_socket: tuple[WebSocket, list[dict[str, object]]],
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """An OSError enumerating the root is an operational fault, not a rejection.

        Per-entry stat faults are skipped in the store, but a fault reading the
        root itself is a server-side failure -- it must not escape to the router's
        broad except (which logs and drops the socket), and it is not a client
        rejection. The handler routes it through WireReply.fault: an id-stamped
        clean error frame audited at ERROR "operation failed", never WARNING
        "rejected op". Reaching the assertions without a raise is the connection-
        intact check.
        """

        def boom(_self: RecordStore) -> Iterator[list[RecordingEntry]]:
            raise OSError("permission denied on recordings root")

        monkeypatch.setattr(RecordStore, "entry_batches", boom)
        ws, sent = recording_socket
        with caplog.at_level(logging.WARNING, logger="punt_vox.voxd.wire_reply"):
            asyncio.run(RecListHandler(recording_store)({"id": "l9"}, ws))
        assert sent[-1]["type"] == "error"
        assert sent[-1]["id"] == "l9"
        # OSError with no in-jail filename -> generic wire verdict, detail to log.
        assert sent[-1]["message"] == "operation failed"
        assert any(
            r.levelno == logging.ERROR
            and "operation failed" in r.getMessage()
            and "permission denied on recordings root" in r.getMessage()
            for r in caplog.records
        )
        assert not any("rejected op" in r.getMessage() for r in caplog.records)

    def test_does_not_recurse_into_subdirs(
        self,
        recording_store: RecordStore,
        recording_socket: tuple[WebSocket, list[dict[str, object]]],
    ) -> None:
        (recording_store.root / "top.mp3").write_bytes(b"1")
        sub = recording_store.root / "sub"
        sub.mkdir()
        (sub / "deep.mp3").write_bytes(b"1")
        ws, sent = recording_socket
        asyncio.run(RecListHandler(recording_store)({"id": "l1"}, ws))
        entries = sent[-1]["entries"]
        assert isinstance(entries, list)
        assert {e["name"] for e in entries} == {"top.mp3"}  # subdir not listed
