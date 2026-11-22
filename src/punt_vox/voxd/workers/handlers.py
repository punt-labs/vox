"""Worker RPCs are thin adapters over the daemon-owned registry."""

from __future__ import annotations

import asyncio
import subprocess
from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd._parse import parse_required_str
from punt_vox.voxd.hook_context_handlers import HookContextHandlers
from punt_vox.voxd.hook_context_store import HookContextStore
from punt_vox.voxd.wire_reply import WireReply
from punt_vox.voxd.workers.codec import WorkerRecordCodec
from punt_vox.voxd.workers.hooks import WorkerHooks
from punt_vox.voxd.workers.registry import WorkerRegistry
from punt_vox.voxd.workers.steering import WorkerSteering

if TYPE_CHECKING:
    from collections.abc import Callable

    from starlette.websockets import WebSocket

    from punt_vox.voxd.types import MessageHandler
    from punt_vox.voxd.workers.model import WorkerRecord
    from punt_vox.voxd.workers.steering_types import SteerReceipt
    from punt_vox.voxd.workers.types import WorkerPayload


@final
class WorkerHandlers:
    """Serialize mutations while running blocking provider I/O off the event loop."""

    __slots__ = ("_lock", "_registry", "_steering")
    _registry: WorkerRegistry
    _lock: asyncio.Lock
    _steering: WorkerSteering

    def __new__(cls, registry: WorkerRegistry) -> Self:
        """Bind one registry and its serialization lock."""
        self = super().__new__(cls)
        self._registry = registry
        self._lock = asyncio.Lock()
        self._steering = WorkerSteering(registry, self._lock)
        return self

    @classmethod
    def build(
        cls,
        registry: WorkerRegistry,
        store: HookContextStore | None = None,  # PY-TS-14: absent omits hook RPCs.
    ) -> dict[str, MessageHandler]:
        """Construct one serialized actor and expose its worker control surface."""
        return cls(registry).handlers(store)

    def handlers(
        self,
        store: HookContextStore | None = None,  # PY-TS-14: absent omits hook RPCs.
    ) -> dict[str, MessageHandler]:
        """Expose this actor's worker RPCs, plus hook ingestion when a store exists."""
        result: dict[str, MessageHandler] = dict.fromkeys(
            ("worker_start", "worker_list", "worker_stop", "worker_steer"),
            self.handle,
        )
        if store is not None:
            hooks = WorkerHooks(store, self._registry, self._lock)
            result.update(HookContextHandlers(store, hooks.ingest).handlers())
            result["worker_hook_admission"] = hooks.admit
        return result

    def projects(self) -> tuple[str, ...]:
        """Return the allowlisted repositories a worker may be started in."""
        return self._registry.projects()

    async def start(self, task: str, project: str) -> WorkerPayload:
        """Start a worker on the default provider and harness."""
        return await self._serialized(self._registry.start, task, project)

    async def stop(self, worker_id: str) -> WorkerPayload:
        """Stop one worker; its worktree and branch are retained."""
        return await self._serialized(self._registry.stop, worker_id)

    async def workers(self) -> list[WorkerPayload]:
        """Reconcile and return every known worker."""
        async with self._lock:
            records = await asyncio.to_thread(self._registry.list)
        return [WorkerRecordCodec.encode(record) for record in records]

    async def steer(self, worker_id: str, words: str, *, urgent: bool) -> SteerReceipt:
        """Deliver words verbatim and report whether the worker heard them."""
        return await self._steering.handle(
            {"worker_id": worker_id, "words": words, "urgent": urgent}
        )

    async def _serialized(
        self, operation: Callable[..., WorkerRecord], *args: str
    ) -> WorkerPayload:
        async with self._lock:
            record = await asyncio.to_thread(operation, *args)
        return WorkerRecordCodec.encode(record)

    async def _dispatch(self, msg: dict[str, object]) -> object:
        match msg["type"]:
            case "worker_start":
                async with self._lock:
                    record = await asyncio.to_thread(
                        self._registry.start,
                        parse_required_str(msg, "task"),
                        parse_required_str(msg, "project"),
                        provider=str(msg.get("provider", "tmux")),
                        harness=str(msg.get("harness", "claude")),
                    )
                return WorkerRecordCodec.encode(record)
            case "worker_stop":
                return await self.stop(parse_required_str(msg, "worker_id"))
            case "worker_list":
                return await self.workers()
            case "worker_steer":
                return await self._steering.handle(msg)
            case _:
                raise ValueError("Unknown worker RPC")

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
