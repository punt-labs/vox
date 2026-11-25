"""Lifecycle RPCs for the daemon's one voice call and its managed agent."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, ClassVar, Final, Self, final

from punt_vox.paths import calls_dir
from punt_vox.voxd.voice_agent.fault import ConvAIError
from punt_vox.voxd.voice_agent.session import Conversation
from punt_vox.voxd.voice_call.active_call import ActiveCall
from punt_vox.voxd.voice_call.agent_operations import AgentOperations
from punt_vox.voxd.voice_call.call_id import CallId
from punt_vox.voxd.voice_call.launcher import CallLauncher
from punt_vox.voxd.wire_reply import WireReply

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractContextManager
    from uuid import UUID

    from starlette.websockets import WebSocket

    from punt_vox.voxd.playback import PlaybackQueue
    from punt_vox.voxd.types import MessageHandler
    from punt_vox.voxd.voice_call.contracts import ProjectAllowlist, WorkerControl
    from punt_vox.voxd.voice_call.progress import ProgressEvents

__all__ = ["CallHandlers"]

_CONNECT_TIMEOUT_S: Final = 30.0  # Provisioning the agent and the handshake.


@final
class CallHandlers:
    """Start, stop and report the daemon's one voice call."""

    __slots__ = ("_active", "_launcher", "_lock")
    _launcher: CallLauncher
    _active: ActiveCall | None  # None before the first call.
    _lock: asyncio.Lock
    ROUTES: ClassVar[tuple[str, ...]] = (
        "call_start",
        "call_start_scoped",
        "call_stop",
        "call_stop_scoped",
        "call_status",
        "call_teardown",
    )

    def __new__(cls, launcher: CallLauncher) -> Self:
        """Route the call RPCs to ``launcher``'s calls."""
        self = super().__new__(cls)
        self._launcher, self._active, self._lock = launcher, None, asyncio.Lock()
        return self

    @classmethod
    def compose(
        cls,
        workers: WorkerControl,
        projects: ProjectAllowlist,
        playback: PlaybackQueue,
        *,
        progress: ProgressEvents,
        hold_music: Callable[[], AbstractContextManager[None]],
    ) -> Self:
        """Wire the production handlers: ElevenLabs from the env key, user paths."""
        launcher = CallLauncher(
            workers,
            projects,
            playback,
            AgentOperations(None),
            progress=progress,
            hold_music=hold_music,
            trace_dir=calls_dir(),
            opener=Conversation.open,
        )
        return cls(launcher)

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
                        status = await self._stop(CallId.requested(msg))
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
            await self._launcher.teardown()
            return self._status()

    async def _start(self, msg: dict[str, object]) -> dict[str, object]:
        """Launch under the lock, then wait unlocked so call_stop can cancel."""
        async with self._lock:
            active = await self._launch(msg)
        await active.wait_connected(_CONNECT_TIMEOUT_S)
        return active.status()

    async def _launch(self, msg: dict[str, object]) -> ActiveCall:
        self._require_idle()
        call_id = CallId.for_start(msg)
        self._active = await self._launcher.launch(call_id, msg)
        return self._active

    async def _stop(self, call_id: UUID | None) -> dict[str, object]:
        active = self._active
        if active is None:
            return {"state": "idle"}
        await active.cancel(call_id)
        return active.status()
