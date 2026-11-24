"""Tests for punt_vox.voxd.chunked_fetch -- the ordered, bounded get transport.

These assert the properties of ``docs/vox-chunked-transfer.tex`` by name against a
real file under ``tmp_path`` and a fake WebSocket capturing frames, driving
``ChunkedTransfer`` with a small chunk bound so multi-chunk streams are cheap:
ordered-contiguous seq, the reassembled total equal to the declared bytes, the
empty-file case, the sha256 the client verifies, and the atomic terminal -- a
mid-stream fault ends with an ``error`` frame and no ``fetch_end``.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import logging
from threading import Event, get_ident
from typing import TYPE_CHECKING, final

import pytest
from starlette.websockets import WebSocketDisconnect

from punt_vox.voxd.chunked_fetch import ChunkedTransfer
from punt_vox.voxd.fetch_digest import FetchDigest
from punt_vox.voxd.wire_reply import WireReply

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

_CHUNK = 4  # a tiny bound so a handful of bytes spans several frames


def _capturing_ws(
    on_begin: Callable[[], object] | None = None,
) -> tuple[object, list[dict[str, object]]]:
    sent: list[dict[str, object]] = []

    @final
    class _WS:
        async def send_json(self, payload: dict[str, object]) -> None:
            sent.append(payload)
            if on_begin is not None and payload["type"] == "fetch_begin":
                on_begin()

    return _WS(), sent


def _run(
    path: Path, ref: str, *, on_begin: Callable[[], object] | None = None
) -> list[dict[str, object]]:
    ws, sent = _capturing_ws(on_begin)
    reply = WireReply(ws, "f1")  # type: ignore[arg-type]
    asyncio.run(ChunkedTransfer(reply, FetchDigest(_CHUNK)).stream(path, ref))
    return sent


def _reassemble(frames: list[dict[str, object]]) -> bytes:
    """Walk begin -> chunk* -> end, returning the reassembled bytes."""
    assert frames[0]["type"] == "fetch_begin"
    assert frames[-1]["type"] == "fetch_end"
    data = b""
    for frame in frames[1:-1]:
        assert frame["type"] == "chunk"
        data += base64.b64decode(str(frame["data"]))
    return data


class TestOrderedDelivery:
    def test_measure_reads_off_loop(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "r.mp3"
        contents = b"abcdefghij"
        path.write_bytes(contents)
        read_on: list[int] = []

        @final
        class _TrackingFile(io.BytesIO):
            def read(self, size: int | None = -1, /) -> bytes:
                read_on.append(get_ident())
                return super().read(size)

        def open_file(_: Path) -> io.BytesIO:
            return _TrackingFile(contents)

        loop_thread = get_ident()
        monkeypatch.setattr(FetchDigest, "_open", staticmethod(open_file))

        during_measure: list[int] = []
        frames = _run(path, "r.mp3", on_begin=lambda: during_measure.extend(read_on))

        assert during_measure and loop_thread not in during_measure
        assert _reassemble(frames) == contents

    def test_replaced_path_sends_the_measured_file(self, tmp_path: Path) -> None:
        path = tmp_path / "r.mp3"
        contents = b"abcdefghij"
        path.write_bytes(contents)
        replacement = tmp_path / "new.mp3"
        replacement.write_bytes(b"0123456789")

        frames = _run(path, path.name, on_begin=lambda: replacement.replace(path))

        assert _reassemble(frames) == contents
        assert frames[0]["sha256"] == hashlib.sha256(contents).hexdigest()

    def test_ordered_contiguous_seq(self, tmp_path: Path) -> None:
        """Every chunk carries the next seq: 0, 1, ... with no gap or repeat."""
        path = tmp_path / "r.mp3"
        path.write_bytes(b"abcdefghij")  # 10 bytes, chunk 4 -> 3 frames
        frames = _run(path, "r.mp3")
        seqs = [f["seq"] for f in frames if f["type"] == "chunk"]
        assert seqs == [0, 1, 2]

    def test_each_chunk_within_bound(self, tmp_path: Path) -> None:
        path = tmp_path / "r.mp3"
        path.write_bytes(b"abcdefghij")
        frames = _run(path, "r.mp3")
        for frame in frames:
            if frame["type"] == "chunk":
                assert len(base64.b64decode(str(frame["data"]))) <= _CHUNK

    def test_total_equals_declared(self, tmp_path: Path) -> None:
        """fetch_begin/end declare the byte count and it equals the reassembly."""
        path = tmp_path / "r.mp3"
        blob = b"\xff\xfb\x90\x00" * 1000  # 4000 bytes, many chunks
        path.write_bytes(blob)
        frames = _run(path, "r.mp3")
        assert frames[0]["bytes"] == len(blob)
        assert frames[-1]["bytes"] == len(blob)
        assert _reassemble(frames) == blob

    def test_reassembles_any_size(self, tmp_path: Path) -> None:
        """A file spanning 1, 2, and many chunks round-trips byte-correct."""
        for size in (1, _CHUNK, _CHUNK + 1, 5 * _CHUNK + 3):
            path = tmp_path / f"r{size}.mp3"
            blob = bytes(range(256)) * size
            path.write_bytes(blob[:size])
            frames = _run(path, path.name)
            assert _reassemble(frames) == blob[:size]

    def test_empty_file_begins_and_ends_with_no_chunks(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.mp3"
        path.write_bytes(b"")
        frames = _run(path, "empty.mp3")
        assert frames[0]["type"] == "fetch_begin"
        assert frames[0]["bytes"] == 0
        assert frames[0]["chunks"] == 0
        assert frames[-1]["type"] == "fetch_end"
        assert [f for f in frames if f["type"] == "chunk"] == []

    def test_sha256_matches_content(self, tmp_path: Path) -> None:
        path = tmp_path / "r.mp3"
        blob = b"the quick brown fox"
        path.write_bytes(blob)
        frames = _run(path, "r.mp3")
        assert frames[0]["sha256"] == hashlib.sha256(blob).hexdigest()


class TestDigestConcurrency:
    def test_cancel_stops_next_read_and_releases_slot(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "r.mp3"
        path.write_bytes(b"abcdefghij")
        original_open = FetchDigest._open
        unblock = Event()
        closed: list[bool] = []
        reads: list[int | None] = []
        opened: list[Path] = []

        async def exercise() -> None:
            loop = asyncio.get_running_loop()
            both_hashing = asyncio.Event()
            started = 0
            loop_errors: list[object] = []
            loop.set_exception_handler(lambda _, context: loop_errors.append(context))

            def note_start() -> None:
                nonlocal started
                started += 1
                if started == 2:
                    both_hashing.set()

            @final
            class _BlockedFile(io.BytesIO):
                def read(self, size: int | None = -1, /) -> bytes:
                    reads.append(size)
                    loop.call_soon_threadsafe(note_start)
                    assert unblock.wait(5), "test did not release the hash worker"
                    return super().read(size)

                def close(self) -> None:
                    closed.append(True)
                    super().close()

            def open_file(file: Path) -> object:
                opened.append(file)
                if len(opened) <= 2:
                    return _BlockedFile(b"abcdefghij")
                return original_open(file)

            monkeypatch.setattr(FetchDigest, "_open", staticmethod(open_file))
            digest = FetchDigest(_CHUNK)
            ws, sent = _capturing_ws()
            reply = WireReply(ws, "f1")  # type: ignore[arg-type]
            tasks = [
                asyncio.create_task(
                    ChunkedTransfer(reply, digest).stream(path, path.name)
                )
                for _ in range(3)
            ]
            try:
                await asyncio.wait_for(both_hashing.wait(), timeout=5)
                tasks[0].cancel()
                with pytest.raises(asyncio.CancelledError):
                    await tasks[0]
                tasks[1].cancel()
                with pytest.raises(asyncio.CancelledError):
                    await tasks[1]
                assert len(opened) == 2
            finally:
                unblock.set()
            await asyncio.wait_for(tasks[2], timeout=5)
            assert _reassemble(sent) == b"abcdefghij"
            assert not loop_errors

        asyncio.run(exercise())
        assert reads == [_CHUNK, _CHUNK]
        assert len(closed) == 2


class TestAtomicTerminal:
    def test_mid_stream_fault_ends_with_error_not_end(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A read fault after fetch_begin ends with an error terminal, no fetch_end."""
        path = tmp_path / "r.mp3"
        path.write_bytes(b"abcdefghij")
        handle = _FailingReadFile(b"abcdefghij")

        monkeypatch.setattr(FetchDigest, "_open", staticmethod(handle.open_for_test))
        frames = _run(path, "r.mp3", on_begin=handle.fail)

        assert frames[0]["type"] == "fetch_begin"
        assert frames[-1]["type"] == "error"
        assert not [f for f in frames if f["type"] == "fetch_end"]

    def test_measure_fault_rejects_before_begin(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A fault while measuring is refused before any fetch_begin or chunk."""
        path = tmp_path / "r.mp3"
        path.write_bytes(b"abcdefghij")

        def boom(_: Path) -> object:
            raise OSError("cannot open")

        monkeypatch.setattr(FetchDigest, "_open", staticmethod(boom))
        frames = _run(path, "r.mp3")

        assert [f["type"] for f in frames] == ["error"]  # no begin, no chunk

    def test_measure_refuses_a_path_replaced_by_a_symlink(self, tmp_path: Path) -> None:
        target = tmp_path / "target.mp3"
        target.write_bytes(b"abcdefghij")
        path = tmp_path / "r.mp3"
        path.symlink_to(target)

        assert [f["type"] for f in _run(path, path.name)] == ["error"]

    def test_shrunk_file_aborts(self, tmp_path: Path) -> None:
        """A file that yields fewer bytes than declared aborts, never a false end.

        The measure pass sees 10 bytes; the send pass sees a shrunk 3, so the
        stream ends early with ``sent < declared`` and aborts.
        """
        path = tmp_path / "r.mp3"
        path.write_bytes(b"abcdefghij")
        frames = _run(path, "r.mp3", on_begin=lambda: path.write_bytes(b"abc"))

        assert frames[0]["type"] == "fetch_begin"
        assert frames[0]["bytes"] == 10
        assert frames[-1]["type"] == "error"
        assert not [f for f in frames if f["type"] == "fetch_end"]


class TestPeerGone:
    def test_pump_does_not_start_when_peer_disconnects_at_begin(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "r.mp3"
        path.write_bytes(b"abcdefghij")
        handle = _FailingReadFile(b"abcdefghij")
        attempted: list[object] = []

        @final
        class _GoneAtBegin:
            async def send_json(self, payload: dict[str, object]) -> None:
                attempted.append(payload["type"])
                handle.fail()
                raise WebSocketDisconnect(code=1006)

        monkeypatch.setattr(FetchDigest, "_open", staticmethod(handle.open_for_test))
        reply = WireReply(_GoneAtBegin(), "f1")  # type: ignore[arg-type]
        asyncio.run(ChunkedTransfer(reply, FetchDigest(_CHUNK)).stream(path, "r.mp3"))

        assert handle.closed
        assert attempted == ["fetch_begin"]

    def test_pump_stops_encoding_when_peer_disconnects(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A chunk send that finds the peer gone stops the pump at once.

        The next chunk is never read or base64-encoded, and the exchange ends
        silently -- no ``fetch_end`` and no ``error`` terminal, since a normal
        disconnect is not a fault. Without the send-result check the pump would
        keep encoding every remaining chunk into a socket nobody is reading.
        """
        path = tmp_path / "r.mp3"
        path.write_bytes(b"abcdefghij")  # 10 bytes, chunk 4 -> 3 chunks

        encodes = {"n": 0}
        real_b64 = base64.b64encode

        def counting_b64(data: bytes) -> bytes:
            encodes["n"] += 1
            return real_b64(data)

        monkeypatch.setattr(
            "punt_vox.voxd.chunked_fetch.base64.b64encode", counting_b64
        )

        sent: list[dict[str, object]] = []

        @final
        class _GoneAfterFirstChunk:
            async def send_json(self, payload: dict[str, object]) -> None:
                sent.append(payload)
                # The peer drops once the second chunk send is attempted.
                if sum(1 for f in sent if f.get("type") == "chunk") >= 2:
                    raise WebSocketDisconnect(code=1006)

        reply = WireReply(_GoneAfterFirstChunk(), "f1")  # type: ignore[arg-type]
        asyncio.run(ChunkedTransfer(reply, FetchDigest(_CHUNK)).stream(path, "r.mp3"))

        # Chunk 0 (delivered) and chunk 1 (the send that finds the gone peer) are
        # encoded; the pump then stops, so chunk 2 is never encoded.
        assert encodes["n"] == 2
        assert not [f for f in sent if f["type"] == "fetch_end"]
        assert not [f for f in sent if f["type"] == "error"]


class TestGrownFile:
    def test_grown_file_transfers_declared_prefix_bounded(self, tmp_path: Path) -> None:
        """A file grown after measure still transfers exactly its declared prefix.

        The measure pass sees 8 bytes; the send pass sees a grown 20. The stream
        is bounded by the declaration -- exactly the first 8 bytes are sent, never
        the larger on-disk size.
        """
        path = tmp_path / "r.mp3"
        path.write_bytes(b"abcdefgh")
        frames = _run(
            path, "r.mp3", on_begin=lambda: path.write_bytes(b"abcdefgh" + b"X" * 12)
        )

        assert frames[0]["bytes"] == 8
        assert frames[-1]["type"] == "fetch_end"
        assert _reassemble(frames) == b"abcdefgh"


class TestAudit:
    """A read fault is a server-side operational failure, audited as a fault."""

    def test_measure_fault_audits_operation_failed(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A fault before fetch_begin routes through fault: ERROR "operation failed"."""
        path = tmp_path / "r.mp3"
        path.write_bytes(b"abc")

        def boom(_: Path) -> object:
            raise OSError("nope")

        monkeypatch.setattr(FetchDigest, "_open", staticmethod(boom))
        with caplog.at_level(logging.WARNING, logger="punt_vox.voxd.wire_reply"):
            _run(path, "r.mp3")
        assert any(
            r.levelno == logging.ERROR
            and "operation failed" in r.getMessage()
            and "f1" in r.getMessage()
            for r in caplog.records
        )
        assert not any("rejected op" in r.getMessage() for r in caplog.records)

    def test_mid_stream_fault_audits_operation_failed(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A fault after fetch_begin also audits as a fault, never a rejection."""
        path = tmp_path / "r.mp3"
        path.write_bytes(b"abcdefghij")
        handle = _FailingReadFile(b"abcdefghij")
        monkeypatch.setattr(FetchDigest, "_open", staticmethod(handle.open_for_test))
        with caplog.at_level(logging.WARNING, logger="punt_vox.voxd.wire_reply"):
            _run(path, "r.mp3", on_begin=handle.fail)
        assert any(
            r.levelno == logging.ERROR and "operation failed" in r.getMessage()
            for r in caplog.records
        )
        assert not any("rejected op" in r.getMessage() for r in caplog.records)


@final
class _FailingReadFile(io.BytesIO):
    _failing = False

    def open_for_test(self, _: Path) -> _FailingReadFile:
        return self

    def fail(self) -> None:
        self._failing = True

    def read(self, size: int | None = -1, /) -> bytes:
        if self._failing:
            raise OSError("read fault")
        return super().read(size)
