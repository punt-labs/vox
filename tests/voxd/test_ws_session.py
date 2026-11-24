"""Exercise fetch cancellation and pipelined replies through the WebSocket route."""

from __future__ import annotations

import asyncio
import io
import json
from threading import Event
from typing import TYPE_CHECKING, final
from unittest.mock import AsyncMock, Mock

import pytest
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from punt_vox.types_audio import FETCH_CHUNK_BYTES
from punt_vox.voxd import recording_handlers
from punt_vox.voxd.fetch_digest import FetchDigest
from punt_vox.voxd.record_store import RecordingEntry, RecordStore
from punt_vox.voxd.recording_handlers import RecordingHandlers
from punt_vox.voxd.router import WebSocketRouter

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture
def router(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> WebSocketRouter:
    monkeypatch.setattr(recording_handlers, "recordings_dir", lambda: tmp_path)
    handlers = RecordingHandlers.build(Mock(), Mock(), Mock())
    return WebSocketRouter(handlers=handlers, auth_token=None)


@final
class _FetchPeer:
    """Drive the router's receive and send sides independently."""

    __slots__ = ("_inbox", "_receive_failed", "_sent", "_task")

    _inbox: asyncio.Queue[str | Exception]
    _receive_failed: asyncio.Event
    _sent: list[dict[str, object]]
    _task: asyncio.Task[None]

    def __new__(cls, router: WebSocketRouter) -> _FetchPeer:
        self = super().__new__(cls)
        self._inbox = asyncio.Queue()
        self._receive_failed = asyncio.Event()
        self._sent = []
        socket = AsyncMock(spec=WebSocket)
        socket.headers = {}
        socket.application_state = WebSocketState.CONNECTED
        socket.receive_text.side_effect = self._receive
        socket.send_json.side_effect = self._sent.append
        self._task = asyncio.create_task(router.handle_connection(socket))
        return self

    @property
    def sent(self) -> tuple[dict[str, object], ...]:
        return tuple(frame.copy() for frame in self._sent)

    async def wait_closed(self) -> None:
        await self._task

    async def wait_receive_failure(self) -> None:
        await self._receive_failed.wait()

    def send(self, kind: str, label: str) -> None:
        self._inbox.put_nowait(json.dumps({"type": kind, "id": label, "ref": label}))

    def disconnect(self, error: Exception | None = None) -> None:
        self._inbox.put_nowait(
            error if error is not None else WebSocketDisconnect(1000)
        )

    async def _receive(self) -> str:
        frame = await self._inbox.get()
        if isinstance(frame, Exception):
            self._receive_failed.set()
            raise frame
        return frame


@pytest.mark.parametrize("queued", [False, True], ids=["active", "queued"])
@pytest.mark.parametrize("pipelined", [False, True], ids=["single", "pipelined"])
async def test_fetch_peer_disconnect_stops_measurement(
    router: WebSocketRouter,
    queued: bool,
    pipelined: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active, blocker, waiting = "active.mp3", "blocker.mp3", "waiting.mp3"
    for ref in (active, blocker, waiting):
        (tmp_path / ref).touch()
    loop = asyncio.get_running_loop()
    entered = {ref: asyncio.Event() for ref in (active, blocker, waiting)}
    first_reads: asyncio.Queue[str] = asyncio.Queue()
    reads: list[str] = []
    opened: list[str] = []
    release = Event()
    original_measure = FetchDigest.measure

    async def measure(digest: FetchDigest, path: Path) -> object:
        entered[path.name].set()
        return await original_measure(digest, path)

    def open_file(path: Path) -> io.BytesIO:
        opened.append(path.name)

        @final
        class _File(io.BytesIO):
            def read(self, size: int | None = -1, /) -> bytes:
                reads.append(path.name)
                if self.tell() == 0:
                    loop.call_soon_threadsafe(first_reads.put_nowait, path.name)
                    assert release.wait(5), "test did not release the read"
                return super().read(size)

        return _File(b"x" * (2 * FETCH_CHUNK_BYTES))

    monkeypatch.setattr(FetchDigest, "measure", measure)
    monkeypatch.setattr(FetchDigest, "_open", staticmethod(open_file))
    first, second, peer = (_FetchPeer(router) for _ in range(3))
    first.send("fetch", active)
    second.send("fetch", blocker)

    try:
        async with asyncio.timeout(2):
            assert {await first_reads.get(), await first_reads.get()} == {
                active,
                blocker,
            }
            if queued:
                peer.send("fetch", waiting)
                await entered[waiting].wait()
                abandoned = peer
            else:
                abandoned = first
            if pipelined:
                abandoned.send("fetch", waiting)
            abandoned.disconnect()
            await asyncio.shield(abandoned.wait_closed())
        assert not abandoned.sent
    finally:
        release.set()
        for connection in (first, second, peer):
            connection.disconnect()
        await asyncio.wait_for(
            asyncio.gather(
                first.wait_closed(), second.wait_closed(), peer.wait_closed()
            ),
            timeout=5,
        )
        # Keep the loop alive until the off-loop digest workers exit.
        await loop.shutdown_default_executor()

    assert waiting not in opened
    if not queued:
        assert reads.count(active) == 1


async def test_rec_list_peer_disconnect_stops_scan_between_batches(
    router: WebSocketRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    release = Event()
    batches: list[int] = []

    def scan(_store: RecordStore) -> Iterator[list[RecordingEntry]]:
        batches.append(1)
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5), "test did not release the scan"
        yield [RecordingEntry("first.mp3", 0)]
        batches.append(2)
        yield [RecordingEntry("second.mp3", 0)]

    monkeypatch.setattr(RecordStore, "entry_batches", scan)
    peer = _FetchPeer(router)
    peer.send("rec_list", "r")
    try:
        async with asyncio.timeout(2):
            await entered.wait()
            peer.disconnect()
            await asyncio.shield(peer.wait_closed())
        assert not peer.sent
    finally:
        release.set()
        await asyncio.wait_for(peer.wait_closed(), timeout=5)
        await loop.shutdown_default_executor()
    assert batches == [1]


@pytest.mark.parametrize("broken_receive", [False, True])
async def test_fetch_keeps_pipelined_replies_in_request_order(
    router: WebSocketRouter,
    broken_receive: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    (tmp_path / "r.mp3").write_bytes(b"data")
    started, resume, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original_measure = FetchDigest.measure

    async def measure(digest: FetchDigest, path: Path) -> object:
        started.set()
        await resume.wait()
        return await original_measure(digest, path)

    async def next_request(msg: dict[str, object], websocket: WebSocket) -> None:
        await websocket.send_json({"type": "next", "id": msg["id"]})
        finished.set()

    monkeypatch.setattr(FetchDigest, "measure", measure)
    router.handlers["next"] = next_request
    peer = _FetchPeer(router)
    try:
        async with asyncio.timeout(2):
            peer.send("fetch", "r.mp3")
            await started.wait()
            peer.send("next", "after")
            if broken_receive:
                peer.disconnect(RuntimeError("broken receive"))
                await peer.wait_receive_failure()
            resume.set()
            await finished.wait()
    finally:
        resume.set()
        peer.disconnect()
        await asyncio.wait_for(peer.wait_closed(), timeout=5)

    assert [frame["type"] for frame in peer.sent] == [
        "fetch_begin",
        "chunk",
        "fetch_end",
        "next",
    ]
    assert caplog.messages.count("WebSocket error") == int(broken_receive)


async def test_fetch_read_ahead_pauses_at_32_and_drains_in_order(
    router: WebSocketRouter,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "r.mp3").write_bytes(b"data")
    full, overflow, resume = (asyncio.Event() for _ in range(3))
    original_measure = FetchDigest.measure
    sent: list[dict[str, object]] = []
    socket = AsyncMock(spec=WebSocket)
    socket.headers = {}
    socket.application_state = WebSocketState.CONNECTED
    socket.send_json.side_effect = sent.append
    labels = [str(i) for i in range(33)]
    frames = iter(
        [
            json.dumps({"type": "fetch", "id": "r", "ref": "r.mp3"}),
            *(json.dumps({"type": "next", "id": label}) for label in labels),
        ]
    )

    async def receive() -> str:
        frame = next(frames, None)
        if frame is None:
            raise WebSocketDisconnect(1000)
        if socket.receive_text.await_count == 33:
            full.set()
        elif socket.receive_text.await_count == 34:
            overflow.set()
        return frame

    async def measure(digest: FetchDigest, path: Path) -> object:
        await resume.wait()
        return await original_measure(digest, path)

    async def next_request(msg: dict[str, object], websocket: WebSocket) -> None:
        if msg["id"] == labels[0]:
            assert socket.receive_text.await_count == 33  # fetch and 32 frames
        await websocket.send_json({"type": "next", "id": msg["id"]})

    socket.receive_text.side_effect = receive
    monkeypatch.setattr(FetchDigest, "measure", measure)
    router.handlers["next"] = next_request
    task = asyncio.create_task(router.handle_connection(socket))
    try:
        async with asyncio.timeout(2):
            await full.wait()
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(overflow.wait(), timeout=0.05)
            assert not sent
            resume.set()
            await asyncio.shield(task)
    finally:
        resume.set()
        await asyncio.wait_for(task, timeout=5)

    assert [frame["type"] for frame in sent[:3]] == [
        "fetch_begin",
        "chunk",
        "fetch_end",
    ]
    assert [frame["id"] for frame in sent[3:]] == labels


@pytest.mark.parametrize("tied", [False, True], ids=["fetch-first", "both-ready"])
async def test_fetch_completion_preserves_the_outstanding_read(
    router: WebSocketRouter, tmp_path: Path, tied: bool
) -> None:
    (tmp_path / "r.mp3").write_bytes(b"data")
    receive_waiting, ending, finish, second_ended = (asyncio.Event() for _ in range(4))
    receive_close = asyncio.Event()
    socket = AsyncMock(spec=WebSocket)
    socket.headers = {}
    socket.application_state = WebSocketState.CONNECTED
    sent: list[dict[str, object]] = []

    async def receive() -> str:
        index = socket.receive_text.await_count
        if index == 2 and tied:
            receive_waiting.set()
            await finish.wait()
        if index <= 2:
            return json.dumps({"type": "fetch", "id": str(index), "ref": "r.mp3"})
        receive_waiting.set()
        await receive_close.wait()
        raise WebSocketDisconnect(1000)

    async def send(frame: dict[str, object]) -> None:
        if frame["type"] == "fetch_end":
            if frame["id"] == "1":
                ending.set()
                await finish.wait()
            else:
                second_ended.set()
        sent.append(frame)

    socket.receive_text.side_effect = receive
    socket.send_json.side_effect = send
    task = asyncio.create_task(router.handle_connection(socket))
    try:
        async with asyncio.timeout(2):
            await ending.wait()
            await receive_waiting.wait()
            finish.set()
            await second_ended.wait()
            assert socket.receive_text.await_count == 3
    finally:
        finish.set()
        receive_close.set()
        await asyncio.wait_for(task, timeout=5)

    assert [(frame["id"], frame["type"]) for frame in sent] == [
        (label, kind)
        for label in ("1", "2")
        for kind in ("fetch_begin", "chunk", "fetch_end")
    ]


async def test_fetch_completion_yields_to_a_ready_disconnect(
    router: WebSocketRouter, tmp_path: Path
) -> None:
    (tmp_path / "r.mp3").write_bytes(b"data")
    receive_waiting, ending, finish = (asyncio.Event() for _ in range(3))
    socket = AsyncMock(spec=WebSocket)
    socket.headers = {}
    socket.application_state = WebSocketState.CONNECTED
    sent: list[dict[str, object]] = []

    async def receive() -> str:
        match socket.receive_text.await_count:
            case 1:
                return json.dumps({"type": "fetch", "id": "r", "ref": "r.mp3"})
            case 2:
                return json.dumps({"type": "next", "id": "buffered"})
            case _:
                receive_waiting.set()
                await finish.wait()
                raise WebSocketDisconnect(1000)

    async def send(frame: dict[str, object]) -> None:
        if frame["type"] == "fetch_end":
            ending.set()
            await finish.wait()
        sent.append(frame)

    async def next_request(msg: dict[str, object], websocket: WebSocket) -> None:
        await websocket.send_json({"type": "next", "id": msg["id"]})

    socket.receive_text.side_effect = receive
    socket.send_json.side_effect = send
    router.handlers["next"] = next_request
    task = asyncio.create_task(router.handle_connection(socket))
    try:
        async with asyncio.timeout(2):
            await ending.wait()
            await receive_waiting.wait()
            finish.set()
            await asyncio.shield(task)
    finally:
        finish.set()
        await asyncio.wait_for(task, timeout=5)

    assert [frame["type"] for frame in sent] == ["fetch_begin", "chunk", "fetch_end"]
    assert socket.receive_text.await_count == 3


async def test_fetch_teardown_failure_is_logged_on_disconnect(
    router: WebSocketRouter,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    (tmp_path / "r.mp3").touch()
    started = asyncio.Event()

    async def measure(_digest: FetchDigest, _path: Path) -> object:
        started.set()
        try:
            return await asyncio.Future[object]()
        except asyncio.CancelledError as exc:
            raise RuntimeError("digest teardown failed") from exc

    monkeypatch.setattr(FetchDigest, "measure", measure)
    peer = _FetchPeer(router)
    try:
        async with asyncio.timeout(2):
            peer.send("fetch", "r.mp3")
            await started.wait()
    finally:
        peer.disconnect()
        await asyncio.wait_for(peer.wait_closed(), timeout=5)

    assert not peer.sent
    (record,) = [
        entry for entry in caplog.records if entry.message == "Fetch teardown failed"
    ]
    assert record.exc_info is not None
    assert str(record.exc_info[1]) == "digest teardown failed"
