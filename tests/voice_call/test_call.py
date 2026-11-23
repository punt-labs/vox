from __future__ import annotations

import asyncio
import base64
import functools
import json
import threading
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock

from starlette.websockets import WebSocket
from websockets.asyncio.server import serve

from punt_vox.voxd.voice_agent import Conversation
from punt_vox.voxd.voice_agent.fault import ConvAIError
from punt_vox.voxd.voice_call.handlers import CallHandlers
from punt_vox.voxd.voice_call.return_brief import ReturnBrief

from .fakes import FakePlayback, FakeWorkers, payload

if TYPE_CHECKING:
    from pathlib import Path

    import pytest
    from websockets.asyncio.server import ServerConnection

    from punt_vox.voxd.playback import PlaybackQueue

_METADATA = {
    "type": "conversation_initiation_metadata",
    "conversation_initiation_metadata_event": {
        "conversation_id": "conv_call",
        "agent_output_audio_format": "pcm_16000",
        "user_input_audio_format": "pcm_16000",
    },
}
_LOOPBACK = functools.partial(Conversation.open, allow_loopback=True)


def _audio(pcm: bytes, event_id: int) -> str:
    body = {"audio_base_64": base64.b64encode(pcm).decode(), "event_id": event_id}
    return json.dumps({"type": "audio", "audio_event": body})


def _start(call_id: str) -> str:
    call = {
        "tool_call_id": call_id,
        "tool_name": "start_worker",
        "parameters": {"task": "add greeting.py"},
        "expects_response": True,
    }
    return json.dumps({"type": "client_tool_call", "client_tool_call": call})


class _Connector:
    def __init__(self, url: str) -> None:
        self.url = url

    def connection(self, tools: object) -> tuple[str, tuple[str, ...]]:
        del tools
        return self.url, ("tool_a",)


async def _rpc(handlers: CallHandlers, msg: dict[str, object]) -> dict[str, object]:
    ws = AsyncMock(spec=WebSocket)
    await handlers.handle({"id": "r", **msg}, ws)
    return cast("dict[str, object]", ws.send_json.call_args.args[0])


async def test_call_barge_in_reask_starts_one_worker(tmp_path: Path) -> None:
    workers, playback = FakeWorkers(), FakePlayback()
    workers.gate.clear()
    results: list[dict[str, object]] = []
    init: list[dict[str, Any]] = []
    audio_chunks = 0
    done = asyncio.Event()

    async def peer(socket: ServerConnection) -> None:
        nonlocal audio_chunks
        init.append(json.loads(await socket.recv()))
        await socket.send(json.dumps(_METADATA))
        await socket.send(_audio(b"\x01\x00" * 8, 1))
        await socket.send(_start("call_1"))
        await socket.send('{"type":"interruption","interruption_event":{"event_id":1}}')
        await socket.send(_audio(b"\x02\x00" * 8, 1))  # late audio of the cut turn
        await socket.send(_start("call_2"))  # the re-ask after the barge-in
        workers.gate.set()
        while len(results) < 2:
            message = json.loads(await socket.recv())
            if message.get("type") == "client_tool_result":
                results.append(message)
            else:
                audio_chunks += "user_audio_chunk" in message
        await socket.send(_audio(b"\x03\x00" * 8, 2))
        await done.wait()

    pcm = tmp_path / "turn.pcm"
    pcm.write_bytes(bytes(640 * 3))
    async with asyncio.timeout(10), serve(peer, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        handlers = CallHandlers(
            workers,
            workers,
            cast("PlaybackQueue", playback),
            connector=_Connector(
                f"ws://127.0.0.1:{port}/convai?conversation_signature=s"
            ),
            trace_dir=tmp_path / "calls",
            opener=_LOOPBACK,
        )
        assert (await _rpc(handlers, {"type": "call_status"}))["result"] == {
            "state": "idle"
        }
        script = [{"pcm_path": str(pcm), "pause_s": 0.1}]
        started = await _rpc(handlers, {"type": "call_start", "script": script})
        status = cast("dict[str, object]", started["result"])
        assert (status["state"], status["conversation_id"]) == ("live", "conv_call")
        again = await _rpc(handlers, {"type": "call_start", "script": script})
        assert "already running" in str(again["message"])
        while len(results) < 2 or len(playback.sinks) < 2:
            await asyncio.sleep(0.01)
        live = (await _rpc(handlers, {"type": "call_status"}))["result"]
        stopped = (await _rpc(handlers, {"type": "call_stop"}))["result"]
        done.set()

    assert workers.started == [("add greeting.py", "/repo")]
    assert [r["tool_call_id"] for r in results] == ["call_1", "call_2"]
    assert results[0]["result"] == results[1]["result"]
    assert audio_chunks > 0
    prompt = init[0]["conversation_config_override"]["agent"]["prompt"]["prompt"]
    assert "start_worker" in prompt
    first, second = playback.sinks
    assert (first.fed, first.flushed) == ([b"\x01\x00" * 8], 1)
    assert second.fed == [b"\x03\x00" * 8]
    assert cast("dict[str, object]", live)["replayed_tool_calls"] == 1
    final = cast("dict[str, object]", stopped)
    assert final["state"] == "ended"
    trace = (tmp_path / "calls").glob("*.jsonl")
    text = next(trace).read_text()
    assert "client_tool_result" in text
    assert "conversation_signature=s" not in text


async def test_remote_hangup_ends_the_call(tmp_path: Path) -> None:
    async def peer(socket: ServerConnection) -> None:
        await socket.recv()
        await socket.send(json.dumps(_METADATA))

    pcm = tmp_path / "turn.pcm"
    pcm.write_bytes(bytes(640))
    async with asyncio.timeout(10), serve(peer, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        workers = FakeWorkers()
        handlers = CallHandlers(
            workers,
            workers,
            cast("PlaybackQueue", FakePlayback()),
            connector=_Connector(f"ws://127.0.0.1:{port}/convai"),
            trace_dir=tmp_path,
            opener=_LOOPBACK,
        )
        await _rpc(handlers, {"type": "call_start", "script": [{"pcm_path": str(pcm)}]})
        while True:
            status = (await _rpc(handlers, {"type": "call_status"}))["result"]
            if cast("dict[str, object]", status)["state"] != "live":
                break
            await asyncio.sleep(0.01)
    assert cast("dict[str, object]", status)["state"] == "ended"


async def test_return_opens_with_only_away_results_and_open_decisions(
    tmp_path: Path,
) -> None:
    workers, playback = FakeWorkers(), FakePlayback()
    workers.known = [
        payload("old", last_assistant_message="Already reviewed.", result_seq=1),
        payload("running", "Write the greeting"),
    ]
    opening: list[str | None] = []

    async def peer(socket: ServerConnection) -> None:
        init = json.loads(await socket.recv())
        opening.append(
            init["conversation_config_override"]["agent"].get("first_message")
        )
        await socket.send(json.dumps(_METADATA))
        await socket.wait_closed()

    pcm = tmp_path / "turn.pcm"
    pcm.write_bytes(bytes(640))
    start: dict[str, object] = {
        "type": "call_start",
        "script": [{"pcm_path": str(pcm)}],
    }
    async with asyncio.timeout(10), serve(peer, "127.0.0.1", 0) as server:

        def handlers() -> CallHandlers:
            return CallHandlers(
                workers,
                workers,
                cast("PlaybackQueue", playback),
                connector=_Connector(
                    f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/convai"
                ),
                trace_dir=tmp_path,
                opener=_LOOPBACK,
            )

        first = handlers()
        assert "result" in await _rpc(first, start)
        assert "result" in await _rpc(first, {"type": "call_stop"})
        workers.known[1] = {
            **payload(
                "running",
                "Run the exact Bash command python3 /private/scripts/gate.py",
                last_assistant_message="Wrote hello.py.",
                result_seq=3,
            ),
            "project": "/private/projects/greeting",
        }
        workers.known.append(
            {
                **payload(
                    "blocked",
                    "Run tests from /private/commands",
                    pending_decision={
                        "notification_type": "idle_prompt",
                        "message": "Which suite?",
                        "seq": 2,
                    },
                ),
                "project": "/private/projects/quality",
            }
        )
        for index in range(4, 7):
            workers.known.append(
                payload(
                    f"new-{index}",
                    f"Task {index}",
                    last_assistant_message=f"Result {index}.",
                    result_seq=index,
                )
            )
        second = handlers()
        assert "result" in await _rpc(second, start)
        assert "result" in await _rpc(second, {"type": "call_stop"})
        workers.known = [row for row in workers.known if row["id"] != "blocked"]
        third = handlers()
        assert "result" in await _rpc(third, start)
        assert "result" in await _rpc(third, {"type": "call_stop"})

    assert opening[0] is None
    assert opening[1] is not None
    assert "Wrote hello.py." in opening[1]
    assert "Which suite?" in opening[1]
    assert opening[1].startswith("While you were away, the worker in ")
    assert "worker in greeting finished: Wrote hello.py." in opening[1]
    assert "worker in quality needs your input: Which suite?" in opening[1]
    assert "Run the exact Bash command" not in opening[1]
    assert "Run tests" not in opening[1]
    assert "/private/" not in opening[1]
    assert "Already reviewed." not in opening[1]
    assert all(f"Result {index}." in opening[1] for index in range(4, 7))
    assert opening[2] is None


def test_return_opener_bounds_long_result_for_speech(tmp_path: Path) -> None:
    brief = ReturnBrief(tmp_path / "departure.json")
    brief.depart([payload("worker")])
    result = payload(
        "worker", last_assistant_message="A long result.\n" * 20, result_seq=1
    )

    options = brief.options([result], ("/repo",), ())

    spoken = " ".join(("A long result.\n" * 20).split())[:159] + "…"
    assert (
        options.first_message
        == f"While you were away, the worker in repo finished: {spoken}"
    )


async def test_start_without_a_key_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    workers = FakeWorkers()
    handlers = CallHandlers(
        workers, workers, cast("PlaybackQueue", FakePlayback()), trace_dir=tmp_path
    )
    pcm = tmp_path / "turn.pcm"
    pcm.write_bytes(bytes(640))
    reply = await _rpc(
        handlers, {"type": "call_start", "script": [{"pcm_path": str(pcm)}]}
    )
    assert "ELEVENLABS_API_KEY" in str(reply["message"])
    assert (await _rpc(handlers, {"type": "call_status"}))["result"] == {
        "state": "idle"
    }


async def test_correction_rewrites_the_interrupted_reply(tmp_path: Path) -> None:
    said = {"agent_response": "On it.", "event_id": 4, "response_id": "r4"}
    again = {"agent_response": "On it.", "event_id": 5, "response_id": "r5"}
    fixed = {
        "original_agent_response": "On it.",
        "corrected_agent_response": "On",
        "event_id": 4,
        "response_id": "r4",
    }

    async def peer(socket: ServerConnection) -> None:
        await socket.recv()
        await socket.send(json.dumps(_METADATA))
        await socket.send(
            json.dumps({"type": "agent_response", "agent_response_event": said})
        )
        await socket.send(
            json.dumps({"type": "agent_response", "agent_response_event": again})
        )
        await socket.send(
            json.dumps(
                {
                    "type": "agent_response_correction",
                    "agent_response_correction_event": fixed,
                }
            )
        )

    pcm = tmp_path / "turn.pcm"
    pcm.write_bytes(bytes(640))
    async with asyncio.timeout(10), serve(peer, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        workers = FakeWorkers()
        handlers = CallHandlers(
            workers,
            workers,
            cast("PlaybackQueue", FakePlayback()),
            connector=_Connector(f"ws://127.0.0.1:{port}/convai"),
            trace_dir=tmp_path,
            opener=_LOOPBACK,
        )
        await _rpc(handlers, {"type": "call_start", "script": [{"pcm_path": str(pcm)}]})
        while True:
            status = cast(
                "dict[str, object]",
                (await _rpc(handlers, {"type": "call_status"}))["result"],
            )
            if status["state"] != "live":
                break
            await asyncio.sleep(0.01)
    assert status["transcript"] == [
        {"role": "agent", "text": "On"},
        {"role": "agent", "text": "On it."},
    ]


async def test_stop_cancels_a_call_still_connecting(tmp_path: Path) -> None:
    hold = asyncio.Event()

    async def peer(socket: ServerConnection) -> None:
        await socket.recv()
        await hold.wait()  # never answers the handshake

    pcm = tmp_path / "turn.pcm"
    pcm.write_bytes(bytes(640))
    async with asyncio.timeout(5), serve(peer, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        workers = FakeWorkers()
        handlers = CallHandlers(
            workers,
            workers,
            cast("PlaybackQueue", FakePlayback()),
            connector=_Connector(f"ws://127.0.0.1:{port}/convai"),
            trace_dir=tmp_path,
            opener=_LOOPBACK,
        )
        script = [{"pcm_path": str(pcm)}]
        start = asyncio.create_task(
            _rpc(handlers, {"type": "call_start", "script": script})
        )
        while (await _rpc(handlers, {"type": "call_status"}))["result"] == {
            "state": "idle"
        }:
            await asyncio.sleep(0.01)
        stopped = (await _rpc(handlers, {"type": "call_stop"}))["result"]
        started = (await start)["result"]
        hold.set()
    assert cast("dict[str, object]", stopped)["state"] == "ended"
    assert cast("dict[str, object]", started)["state"] == "ended"


class _SlowConnector:
    def __init__(self) -> None:
        self.release = threading.Event()

    def connection(self, tools: object) -> tuple[str, tuple[str, ...]]:
        del tools
        self.release.wait(5)
        raise ConvAIError("agent provisioning failed")


async def test_stop_cancels_a_call_still_provisioning(tmp_path: Path) -> None:
    connector = _SlowConnector()
    workers = FakeWorkers()
    handlers = CallHandlers(
        workers,
        workers,
        cast("PlaybackQueue", FakePlayback()),
        connector=connector,
        trace_dir=tmp_path,
        opener=_LOOPBACK,
    )
    async with asyncio.timeout(5):
        start = asyncio.create_task(_rpc(handlers, {"type": "call_start"}))
        while (await _rpc(handlers, {"type": "call_status"}))["result"] == {
            "state": "idle"
        }:
            await asyncio.sleep(0.01)
        stopped = (await _rpc(handlers, {"type": "call_stop"}))["result"]
        connector.release.set()
        await start
    assert cast("dict[str, object]", stopped)["state"] == "ended"


async def test_a_failed_provisioning_is_reported_to_the_caller(
    tmp_path: Path,
) -> None:
    connector = _SlowConnector()
    connector.release.set()
    workers = FakeWorkers()
    handlers = CallHandlers(
        workers,
        workers,
        cast("PlaybackQueue", FakePlayback()),
        connector=connector,
        trace_dir=tmp_path,
        opener=_LOOPBACK,
    )
    reply = await _rpc(handlers, {"type": "call_start"})
    assert "provisioning failed" in str(reply)
    status = (await _rpc(handlers, {"type": "call_status"}))["result"]
    assert cast("dict[str, object]", status)["state"] == "failed"


async def test_a_call_that_never_connects_times_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hold = asyncio.Event()

    async def peer(socket: ServerConnection) -> None:
        await socket.recv()
        await hold.wait()

    monkeypatch.setattr("punt_vox.voxd.voice_call.handlers._CONNECT_TIMEOUT_S", 0.2)
    async with asyncio.timeout(5), serve(peer, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        workers = FakeWorkers()
        handlers = CallHandlers(
            workers,
            workers,
            cast("PlaybackQueue", FakePlayback()),
            connector=_Connector(f"ws://127.0.0.1:{port}/convai"),
            trace_dir=tmp_path,
            opener=_LOOPBACK,
        )
        assert set(handlers.handlers()) == {"call_start", "call_stop", "call_status"}
        reply = await _rpc(handlers, {"type": "call_start"})
        hold.set()
    assert "did not connect" in str(reply)


async def test_stop_without_a_call_reports_idle(tmp_path: Path) -> None:
    workers = FakeWorkers()
    handlers = CallHandlers(
        workers,
        workers,
        cast("PlaybackQueue", FakePlayback()),
        connector=_Connector("ws://unused"),
        trace_dir=tmp_path,
        opener=_LOOPBACK,
    )
    assert (await _rpc(handlers, {"type": "call_stop"}))["result"] == {"state": "idle"}
