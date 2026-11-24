"""Allowlist RPCs: the daemon alone edits worker_repos, on the CLI's behalf."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, ClassVar, Self, final

from punt_vox.voxd._parse import parse_required_str
from punt_vox.voxd.wire_reply import WireReply

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from starlette.websockets import WebSocket

    from punt_vox.voxd.types import MessageHandler
    from punt_vox.voxd.workers.policy import WorkerRepoPolicy


@final
class AllowlistHandlers:
    """Serialize allow, disallow and repos so two edits never interleave."""

    __slots__ = ("_lock", "_policy")
    _policy: WorkerRepoPolicy
    _lock: asyncio.Lock
    ROUTES: ClassVar[tuple[str, ...]] = (
        "worker_allow",
        "worker_disallow",
        "worker_repos",
    )

    def __new__(cls, policy: WorkerRepoPolicy) -> Self:
        """Bind the policy whose config file the daemon owns."""
        self = super().__new__(cls)
        self._policy = policy
        self._lock = asyncio.Lock()
        return self

    def handlers(self) -> dict[str, MessageHandler]:
        """Expose the three allowlist RPCs."""
        return dict.fromkeys(self.ROUTES, self.handle)

    async def _allow(self, msg: dict[str, object]) -> list[str]:
        project = parse_required_str(msg, "project")
        async with self._lock:
            return list(await asyncio.to_thread(self._policy.allow, project))

    async def _disallow(self, msg: dict[str, object]) -> list[str]:
        project = parse_required_str(msg, "project")
        async with self._lock:
            return list(await asyncio.to_thread(self._policy.disallow, project))

    async def _repos(self, _msg: dict[str, object]) -> list[str]:
        async with self._lock:
            return list(await asyncio.to_thread(self._policy.repos))

    async def handle(self, msg: dict[str, object], websocket: WebSocket) -> None:
        """Answer with the new allowlist, or the one-line reason it was refused."""
        operations: dict[str, Callable[[dict[str, object]], Awaitable[list[str]]]] = {
            "worker_allow": self._allow,
            "worker_disallow": self._disallow,
            "worker_repos": self._repos,
        }
        reply = WireReply(websocket, str(msg.get("id", "")))
        operation = operations.get(str(msg.get("type")))
        if operation is None:
            await reply.error("Unknown allowlist RPC")
            return
        try:
            result = await operation(msg)
        except (ValueError, KeyError, OSError) as exc:
            await reply.error(str(exc))
            return
        await reply.send({"type": "worker_result", "result": result})
