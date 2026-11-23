"""Exercise music and call RPCs through their shared daemon wiring."""

from __future__ import annotations

import asyncio
import functools
import json
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, Mock

import pytest
from programs.conftest import QuietProducer, seed_album
from starlette.websockets import WebSocket
from websockets.asyncio.server import serve

from punt_vox.voxd.handler_registry import HandlerRegistry
from punt_vox.voxd.health import DaemonHealth
from punt_vox.voxd.playback import PlaybackQueue
from punt_vox.voxd.programs.wiring import ProgramSubsystem
from punt_vox.voxd.synthesis import SynthesisPipeline
from punt_vox.voxd.voice_agent import Conversation
from punt_vox.voxd.voice_agent.fault import ConvAIError
from punt_vox.voxd.voice_call.agent_operations import AgentOperations
from punt_vox.voxd.workers.handlers import WorkerHandlers
from punt_vox.voxd.workers.wiring import WorkerSubsystem

from .fakes import FakeWorkers

if TYPE_CHECKING:
    from pathlib import Path

    from websockets.asyncio.server import ServerConnection

    from punt_vox.voxd.voice_agent.contracts import ClientTool


@pytest.mark.parametrize(
    ("initial", "outcome"),
    [
        ("playing", "local"),
        ("playing", "remote"),
        ("playing", "failed"),
        ("paused", "local"),
        ("stopped replay", "local"),
        ("stopped program", "local"),
    ],
)
async def test_call_music_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, initial: str, outcome: str
) -> None:
    connecting = asyncio.Event()
    connect = asyncio.Event()
    hangup = asyncio.Event()
    pcm = tmp_path / "turn.pcm"
    pcm.write_bytes(bytes(640))
    seed_album(tmp_path, 1)
    programs = ProgramSubsystem(tmp_path, QuietProducer(), tmp_path / "mpv.sock")
    playback = PlaybackQueue()
    workers = FakeWorkers()
    monkeypatch.setattr(WorkerSubsystem, "recover", Mock(return_value=workers))
    monkeypatch.setattr(WorkerSubsystem, "policy", Mock(return_value=workers))
    monkeypatch.setattr(WorkerHandlers, "handlers", Mock(return_value={}))
    monkeypatch.setattr("punt_vox.voxd.voice_call.handlers.calls_dir", lambda: tmp_path)
    monkeypatch.setattr(AgentOperations, "prepare", AsyncMock())
    monkeypatch.setattr(
        Conversation, "open", functools.partial(Conversation.open, allow_loopback=True)
    )

    async def peer(socket: ServerConnection) -> None:
        await socket.recv()
        await socket.send(
            json.dumps(
                {
                    "type": "conversation_initiation_metadata",
                    "conversation_initiation_metadata_event": {
                        "conversation_id": "conv_call",
                        "agent_output_audio_format": "pcm_16000",
                        "user_input_audio_format": "pcm_16000",
                    },
                }
            )
        )
        await hangup.wait()

    async with asyncio.timeout(5), serve(peer, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]

        async def dial(
            _agent: AgentOperations, _tools: tuple[ClientTool, ...]
        ) -> tuple[str, tuple[str, ...]]:
            connecting.set()
            await connect.wait()
            if outcome == "failed":
                raise ConvAIError("agent provisioning failed")
            return f"ws://127.0.0.1:{port}/convai", ()

        monkeypatch.setattr(AgentOperations, "connection", dial)
        routes = HandlerRegistry(
            synthesis=SynthesisPipeline(playback_mutex=playback.mutex),
            playback=playback,
            programs=programs,
            health=DaemonHealth(playback, lambda: 0, 0),
        ).build()

        async def rpc(kind: str, **fields: object) -> dict[str, object]:
            ws = AsyncMock(spec=WebSocket)
            await routes[kind]({"type": kind, "id": "r", **fields}, ws)
            return cast("dict[str, object]", ws.send_json.call_args.args[0])

        if initial == "stopped program":
            await rpc("program_on")
        else:
            await rpc("program_select", album_id="a3f1c9")
        await programs.service.run_once()
        if initial == "paused":
            await rpc("program_pause")
        elif initial.startswith("stopped"):
            await rpc("program_stop")
            await programs.service.run_once()

        baseline = programs.service.status()
        async with asyncio.TaskGroup() as group:
            start = group.create_task(
                rpc("call_start", script=[{"pcm_path": str(pcm)}])
            )
            await connecting.wait()
            paused_while_connecting = programs.service.status().paused
            connect.set()
            started = await start
            paused_during_call = programs.service.status().paused
            if outcome == "remote":
                hangup.set()
                while (
                    cast("dict[str, object]", (await rpc("call_status"))["result"])[
                        "state"
                    ]
                    == "live"
                ):
                    await asyncio.sleep(0.01)
            elif outcome == "local":
                assert (
                    cast("dict[str, object]", (await rpc("call_stop"))["result"])[
                        "state"
                    ]
                    == "ended"
                )
                hangup.set()

    assert paused_while_connecting == (not initial.startswith("stopped"))
    if outcome == "failed":
        assert "agent provisioning failed" in str(started)
    else:
        assert cast("dict[str, object]", started["result"])["state"] == "live"
        assert paused_during_call == (not initial.startswith("stopped"))
    assert programs.service.status() == baseline
