"""Lifecycle RPCs for the daemon's one voice call and its managed agent."""

from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Final, Self, final

from punt_vox.paths import calls_dir
from punt_vox.voxd.voice_agent.fault import ConvAIError
from punt_vox.voxd.voice_call.active_call import ActiveCall, CallTrace
from punt_vox.voxd.voice_call.agent_operations import AgentOperations
from punt_vox.voxd.voice_call.boss_tools import BossTools
from punt_vox.voxd.voice_call.call import VoiceAgentCall
from punt_vox.voxd.voice_call.cursor_commit import CursorCommit
from punt_vox.voxd.voice_call.inputs import MicInput, ScriptInput
from punt_vox.voxd.voice_call.progress import ProgressEvents
from punt_vox.voxd.voice_call.return_brief import ReturnBrief
from punt_vox.voxd.voice_call.speaker import AgentSpeaker
from punt_vox.voxd.wire_reply import WireReply

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractContextManager
    from uuid import UUID

    from starlette.websockets import WebSocket

    from punt_vox.voxd.playback import PlaybackQueue
    from punt_vox.voxd.types import MessageHandler
    from punt_vox.voxd.voice_agent.contracts import SessionOptions
    from punt_vox.voxd.voice_call.call import Opener
    from punt_vox.voxd.voice_call.connector import AgentConnector
    from punt_vox.voxd.voice_call.contracts import (
        CallInput,
        ProjectAllowlist,
        WorkerControl,
    )

__all__ = ["CallHandlers"]

_CONNECT_TIMEOUT_S: Final = 30.0  # Provisioning the agent and the handshake.
logger = logging.getLogger(__name__)


@final
class CallHandlers:
    """Start, stop and report the daemon's one voice call."""

    __slots__ = (
        "_active",
        "_connector",
        "_hold_music",
        "_lock",
        "_opener",
        "_playback",
        "_progress",
        "_projects",
        "_trace_dir",
        "_workers",
    )
    _workers: WorkerControl
    _progress: ProgressEvents
    _projects: ProjectAllowlist
    _playback: PlaybackQueue
    _connector: AgentOperations
    _hold_music: Callable[[], AbstractContextManager[None]]
    _trace_dir: Path
    _active: ActiveCall | None  # None before the first call.
    _lock: asyncio.Lock
    _opener: Opener | None
    ROUTES: ClassVar[tuple[str, ...]] = (
        "call_start",
        "call_start_scoped",
        "call_stop",
        "call_stop_scoped",
        "call_status",
        "call_teardown",
    )

    def __new__(
        cls,
        workers: WorkerControl,
        projects: ProjectAllowlist,
        playback: PlaybackQueue,
        *,
        connector: AgentConnector | None = None,  # None: ElevenLabs, from the env key.
        trace_dir: Path | None = None,  # None: the user state directory.
        opener: Opener | None = None,
        progress: ProgressEvents | None = None,  # None creates an unconnected source.
        hold_music: Callable[[], AbstractContextManager[None]] = nullcontext,
    ) -> Self:
        """Bind the workers, their allowlist, the playback queue and the agent."""
        self = super().__new__(cls)
        self._workers, self._projects = workers, projects
        self._progress = progress if progress is not None else ProgressEvents()
        self._playback, self._connector = playback, AgentOperations(connector)
        self._hold_music = hold_music
        self._trace_dir = trace_dir if trace_dir is not None else calls_dir()
        self._active = None
        self._lock = asyncio.Lock()
        self._opener = opener
        return self

    def handlers(self) -> dict[str, MessageHandler]:
        """Expose the call lifecycle RPCs."""
        return dict.fromkeys(self.ROUTES, self.handle)

    async def handle(self, msg: dict[str, object], websocket: WebSocket) -> None:
        """Answer one lifecycle RPC with the call's status."""
        reply = WireReply(websocket, str(msg.get("id", "")))
        try:
            match str(msg.get("type")).removesuffix("_scoped"):
                case "call_start":
                    status = await self._start(msg)
                case "call_stop":
                    async with self._lock:
                        status = await self._stop(ActiveCall.requested_id(msg))
                case "call_teardown":
                    status = await self._teardown()
                case _:
                    status = self._status()
        except (ValueError, OSError, ConvAIError) as exc:
            await reply.error(str(exc))
            return
        await reply.send({"type": "call_result", "result": status})

    def _status(self) -> dict[str, object]:
        return self._active.status() if self._active is not None else {"state": "idle"}

    def _require_idle(self) -> None:
        if self._active is not None and self._active.running:
            raise ValueError("a call is already running; stop it first")

    async def _teardown(self) -> dict[str, object]:
        async with self._lock:
            self._require_idle()
            await self._connector.teardown()
            return self._status()

    async def _start(self, msg: dict[str, object]) -> dict[str, object]:
        """Launch under the lock, then wait unlocked so call_stop can cancel."""
        async with self._lock:
            active = await self._launch(msg)
        await active.wait_connected(_CONNECT_TIMEOUT_S)
        return active.status()

    async def _launch(self, msg: dict[str, object]) -> ActiveCall:
        self._require_idle()
        call_id = ActiveCall.requested_id(msg) or uuid.uuid4()
        audio: CallInput = (
            await asyncio.to_thread(ScriptInput.from_wire, msg["script"])
            if "script" in msg
            else MicInput.with_echo_control(msg.get("echo_control"))
        )
        await self._connector.prepare()
        await asyncio.to_thread(CursorCommit.prepare, self._trace_dir)
        trace = CallTrace.reserve(self._trace_dir, call_id)
        returning = ReturnBrief(self._trace_dir / "departure.json")
        call = VoiceAgentCall(
            BossTools(self._workers, self._projects, spoken_labels=returning.labels),
            audio,
            AgentSpeaker(self._playback),
            trace=trace.events,
            opener=self._opener,
            progress=self._progress,
        )

        async def dial() -> tuple[str, SessionOptions]:
            url, tool_ids = await self._connector.connection(BossTools.CATALOG)
            workers = await self._workers.workers()
            options = await asyncio.to_thread(
                returning.options,
                workers,
                self._projects.repos(),
                tool_ids,
            )
            return url, options

        async def depart(reported: dict[str, int]) -> None:
            try:
                await asyncio.to_thread(returning.depart, reported)
            except (OSError, RuntimeError, ValueError) as exc:
                logger.warning("Call departure not saved (%s)", type(exc).__name__)

        async def run() -> None:
            with self._hold_music():
                await call.run(dial, returning.covered, depart=depart)

        task = asyncio.create_task(run())
        task.add_done_callback(lambda _: trace.stream.close())
        self._active = ActiveCall(call_id, call, task, trace.path)
        return self._active

    async def _stop(self, call_id: UUID | None) -> dict[str, object]:
        active = self._active
        if active is None:
            return {"state": "idle"}
        await active.cancel(call_id)
        return active.status()
