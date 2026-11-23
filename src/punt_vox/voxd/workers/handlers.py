"""Worker RPCs are thin adapters over the daemon-owned registry."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING, ClassVar, Self, final

from punt_vox.voxd._parse import parse_required_str
from punt_vox.voxd.hook_context_handlers import HookContextHandlers
from punt_vox.voxd.wire_reply import WireReply
from punt_vox.voxd.workers.actor import WorkerActor

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from starlette.websockets import WebSocket

    from punt_vox.voxd.hook_context_store import HookContextStore
    from punt_vox.voxd.types import MessageHandler
    from punt_vox.voxd.voice_call.progress import ProgressEvents
    from punt_vox.voxd.workers.registry import WorkerRegistry
    from punt_vox.voxd.workers.types import WorkerPayload


@final
class WorkerHandlers:
    """Adapt worker wire RPCs and hook ingestion onto one serialized actor."""

    __slots__ = ("_actor",)
    _actor: WorkerActor
    ROUTES: ClassVar[tuple[str, ...]] = (
        "worker_start",
        "worker_list",
        "worker_stop",
        "worker_steer",
    )

    def __new__(cls, actor: WorkerActor) -> Self:
        """Bind the actor every worker RPC is serialized through."""
        self = super().__new__(cls)
        self._actor = actor
        return self

    @classmethod
    def build(
        cls,
        registry: WorkerRegistry,
        store: HookContextStore | None = None,  # PY-TS-14: absent omits hook RPCs.
    ) -> dict[str, MessageHandler]:
        """Construct one serialized actor and expose its worker control surface."""
        return cls(WorkerActor(registry, store)).handlers(store)

    def handlers(
        self,
        store: HookContextStore | None = None,  # PY-TS-14: absent omits hook RPCs.
        events: ProgressEvents | None = None,  # None omits live report subscribers.
    ) -> dict[str, MessageHandler]:
        """Expose the worker RPCs, plus hook ingestion when a store exists."""
        result: dict[str, MessageHandler] = dict.fromkeys(self.ROUTES, self.handle)
        if store is not None:
            hooks = self._actor.hooks(store, events)
            result.update(HookContextHandlers(store, hooks.ingest).handlers())
            result["worker_hook_admission"] = hooks.admit
        return result

    async def _start(self, msg: dict[str, object]) -> WorkerPayload:
        return await self._actor.start(
            parse_required_str(msg, "task"),
            parse_required_str(msg, "project"),
            provider=str(msg.get("provider", "tmux")),
            harness=str(msg.get("harness", "claude")),
        )

    async def _stop(self, msg: dict[str, object]) -> WorkerPayload:
        return await self._actor.stop(parse_required_str(msg, "worker_id"))

    async def _list(self, _msg: dict[str, object]) -> list[WorkerPayload]:
        return await self._actor.workers()

    async def _dispatch(self, msg: dict[str, object]) -> object:
        operations: dict[str, Callable[[dict[str, object]], Awaitable[object]]] = {
            "worker_start": self._start,
            "worker_stop": self._stop,
            "worker_list": self._list,
            "worker_steer": self._actor.steer_request,
        }
        operation = operations.get(str(msg["type"]))
        if operation is None:
            raise ValueError("Unknown worker RPC")
        return await operation(msg)

    async def handle(self, msg: dict[str, object], websocket: WebSocket) -> None:
        """Return clear transport errors without exposing subprocess output."""
        reply = WireReply(websocket, str(msg.get("id", "")))
        try:
            result = await self._dispatch(msg)
        except (
            ValueError,
            KeyError,
            OSError,
            RuntimeError,
            subprocess.SubprocessError,
        ) as exc:
            await reply.error(str(exc))
            return
        await reply.send({"type": "worker_result", "result": result})
