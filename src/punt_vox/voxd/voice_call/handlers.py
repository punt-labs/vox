"""call_start / call_stop / call_status: the daemon owns at most one Mode B call."""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, Protocol, Self, TextIO, final

from punt_vox.paths import user_state_dir
from punt_vox.voxd.voice_agent.agent import VoiceAgent
from punt_vox.voxd.voice_agent.contracts import SessionOptions
from punt_vox.voxd.voice_agent.control import ConvAIControl
from punt_vox.voxd.voice_agent.fault import ConvAIError
from punt_vox.voxd.voice_agent.session import Conversation
from punt_vox.voxd.voice_agent.trace import EventTrace
from punt_vox.voxd.voice_call.boss_tools import BossTools
from punt_vox.voxd.voice_call.call import VoiceAgentCall
from punt_vox.voxd.voice_call.inputs import MicInput, ScriptInput
from punt_vox.voxd.voice_call.seed import CallSeed
from punt_vox.voxd.voice_call.speaker import AgentSpeaker
from punt_vox.voxd.wire_reply import WireReply

if TYPE_CHECKING:
    from starlette.websockets import WebSocket

    from punt_vox.voxd.playback import PlaybackQueue
    from punt_vox.voxd.types import MessageHandler
    from punt_vox.voxd.voice_agent.contracts import ClientTool
    from punt_vox.voxd.voice_call.call import Dial, Opener
    from punt_vox.voxd.voice_call.contracts import (
        CallInput,
        ProjectAllowlist,
        WorkerControl,
    )

__all__ = ["CallHandlers"]

_CONNECT_TIMEOUT_S: Final = 30.0  # Provisioning the agent and the handshake.


class AgentConnector(Protocol):
    """Provision or reuse the agent and sign a conversation URL (blocking I/O)."""

    def connection(self, tools: tuple[ClientTool, ...]) -> tuple[str, tuple[str, ...]]:
        """Return a signed URL and the registered tool IDs."""
        ...


@dataclass(frozen=True, slots=True)
class _ActiveCall:
    """A running call, its task and the trace file only it writes."""

    call: VoiceAgentCall
    task: asyncio.Task[None]
    trace_path: Path

    def status(self) -> dict[str, object]:
        """Return the call's status and where its trace is written."""
        return {**self.call.status(), "trace": str(self.trace_path)}


@final
class CallHandlers:
    """Start, stop and report the daemon's one Mode B call."""

    __slots__ = (
        "_active",
        "_connector",
        "_lock",
        "_opener",
        "_playback",
        "_projects",
        "_trace_dir",
        "_workers",
    )
    _workers: WorkerControl
    _projects: ProjectAllowlist
    _playback: PlaybackQueue
    _connector: AgentConnector | None  # None until a call reads the env key.
    _trace_dir: Path
    _active: _ActiveCall | None  # None before the first call.
    _lock: asyncio.Lock
    _opener: Opener

    def __new__(
        cls,
        workers: WorkerControl,
        projects: ProjectAllowlist,
        playback: PlaybackQueue,
        *,
        connector: AgentConnector | None = None,  # None: ElevenLabs, from the env key.
        trace_dir: Path | None = None,  # None: the user state directory.
        opener: Opener = Conversation.open,
    ) -> Self:
        """Bind the workers, their allowlist, the playback queue and the agent."""
        self = super().__new__(cls)
        self._workers, self._projects = workers, projects
        self._playback, self._connector = playback, connector
        self._trace_dir = (
            trace_dir if trace_dir is not None else user_state_dir() / "calls"
        )
        self._active = None
        self._lock = asyncio.Lock()
        self._opener = opener
        return self

    def handlers(self) -> dict[str, MessageHandler]:
        """Expose the call lifecycle RPCs."""
        return dict.fromkeys(("call_start", "call_stop", "call_status"), self.handle)

    async def handle(self, msg: dict[str, object], websocket: WebSocket) -> None:
        """Answer one lifecycle RPC with the call's status."""
        reply = WireReply(websocket, str(msg.get("id", "")))
        try:
            match msg.get("type"):
                case "call_start":
                    status = await self._start(msg)
                case "call_stop":
                    async with self._lock:
                        status = await self._stop()
                case _:
                    status = self._status()
        except (ValueError, OSError, ConvAIError) as exc:
            await reply.error(str(exc))
            return
        await reply.send({"type": "call_result", "result": status})

    def _status(self) -> dict[str, object]:
        return self._active.status() if self._active is not None else {"state": "idle"}

    async def _start(self, msg: dict[str, object]) -> dict[str, object]:
        """Launch under the lock, then wait unlocked so call_stop can cancel."""
        async with self._lock:
            active = await self._launch(msg)
        try:
            await asyncio.wait_for(active.call.connected(), _CONNECT_TIMEOUT_S)
        except TimeoutError:
            await self._cancel(active)
            raise ConvAIError("the ElevenLabs conversation did not connect") from None
        status = active.status()
        if status["state"] == "failed":
            raise ConvAIError(str(status["error"]))
        return status

    async def _launch(self, msg: dict[str, object]) -> _ActiveCall:
        if self._active is not None and not self._active.task.done():
            raise ValueError("a call is already running; stop it first")
        agent = self._agent()
        audio: CallInput = (
            await asyncio.to_thread(ScriptInput.from_wire, msg["script"])
            if "script" in msg
            else MicInput()
        )
        self._trace_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        stamp = f"{time.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"
        path = self._trace_dir / f"call-{stamp}.jsonl"
        stream = os.fdopen(
            os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w"
        )
        call = VoiceAgentCall(
            BossTools(self._workers, self._projects),
            audio,
            AgentSpeaker(self._playback),
            trace=EventTrace(stream),
            opener=self._opener,
        )

        async def dial() -> tuple[str, SessionOptions]:
            prompt = CallSeed(self._projects.repos(), await self._workers.workers())
            url, tool_ids = await asyncio.to_thread(agent.connection, BossTools.CATALOG)
            return url, SessionOptions(prompt.prompt(), tool_ids)

        task = asyncio.create_task(self._run(call, dial, stream))
        self._active = _ActiveCall(call, task, path)
        return self._active

    @staticmethod
    async def _run(call: VoiceAgentCall, dial: Dial, stream: TextIO) -> None:
        try:
            await call.run(dial)
        finally:
            stream.close()

    async def _stop(self) -> dict[str, object]:
        if self._active is not None:
            await self._cancel(self._active)
        return self._status()

    @staticmethod
    async def _cancel(active: _ActiveCall) -> None:
        active.task.cancel()
        await asyncio.gather(active.task, return_exceptions=True)

    def _agent(self) -> AgentConnector:
        if self._connector is None:
            key = os.environ.get("ELEVENLABS_API_KEY", "")
            if not key:
                raise ValueError("voxd has no ELEVENLABS_API_KEY for a voice call")
            self._connector = VoiceAgent(ConvAIControl(key))
        return self._connector
