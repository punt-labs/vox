"""Worker RPCs are thin adapters over the daemon-owned registry."""

from __future__ import annotations

import asyncio
import subprocess
from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd._parse import parse_required_str
from punt_vox.voxd.wire_reply import WireReply
from punt_vox.voxd.workers.model import WorkerRecord
from punt_vox.voxd.workers.registry import WorkerRegistry

if TYPE_CHECKING:
    from starlette.websockets import WebSocket

    from punt_vox.voxd.types import MessageHandler


@final
class WorkerHandlers:
    """Serialize mutations while running blocking provider I/O off the event loop."""

    __slots__ = ("_lock", "_registry")
    _registry: WorkerRegistry
    _lock: asyncio.Lock

    def __new__(cls, registry: WorkerRegistry) -> Self:
        """Bind one registry and its serialization lock."""
        self = super().__new__(cls)
        self._registry = registry
        self._lock = asyncio.Lock()
        return self

    def handlers(self) -> dict[str, MessageHandler]:
        """Expose the worker control surface."""
        return dict.fromkeys(
            ("worker_start", "worker_list", "worker_stop"), self.handle
        )

    def _dispatch(self, msg: dict[str, object]) -> WorkerRecord | list[WorkerRecord]:
        match msg["type"]:
            case "worker_start":
                return self._registry.start(
                    parse_required_str(msg, "task"),
                    parse_required_str(msg, "project"),
                    provider=str(msg.get("provider", "tmux")),
                    harness=str(msg.get("harness", "claude")),
                )
            case "worker_stop":
                return self._registry.stop(parse_required_str(msg, "worker_id"))
            case "worker_list":
                return self._registry.list()
            case _:
                raise ValueError("Unknown worker RPC")

    async def handle(self, msg: dict[str, object], websocket: WebSocket) -> None:
        """Return clear transport errors without exposing subprocess output."""
        reply = WireReply(websocket, str(msg.get("id", "")))
        try:
            async with self._lock:
                result = await asyncio.to_thread(self._dispatch, msg)
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
