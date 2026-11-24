"""Allowlist RPCs: the daemon alone edits worker_repos, on the CLI's behalf."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, ClassVar, Self, final

from punt_vox.voxd._parse import parse_optional_str, parse_required_str
from punt_vox.voxd.wire_reply import WireReply
from punt_vox.voxd.workers.repo_spellings import RepoSpellings

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

    @staticmethod
    def _project(msg: dict[str, object]) -> str:
        """Refuse a blank project: an empty path would resolve to the daemon's cwd."""
        project = parse_required_str(msg, "project")
        if not project.strip():
            raise ValueError("project is required")
        return project

    async def _edited(self, operation: Callable[[], tuple[str, ...]]) -> list[str]:
        """Run one policy edit off the loop, under the lock, as a list reply."""
        async with self._lock:
            return list(await asyncio.to_thread(operation))

    async def _allow(self, msg: dict[str, object]) -> list[str]:
        project = self._project(msg)
        return await self._edited(lambda: self._policy.allow(project))

    async def _disallow(self, msg: dict[str, object]) -> list[str]:
        # ``spelling`` is the path as the operator typed it, sent only when
        # the client could not resolve it to the same text.
        named = RepoSpellings(self._project(msg), parse_optional_str(msg, "spelling"))
        return await self._edited(lambda: self._policy.disallow(named))

    async def _repos(self, _msg: dict[str, object]) -> list[str]:
        return await self._edited(self._policy.repos)

    async def handle(self, msg: dict[str, object], websocket: WebSocket) -> None:
        """Answer with the new allowlist, a refusal, or a path-free fault."""
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
        except (ValueError, OSError) as exc:
            # A refused request is an error; the daemon's own config file
            # unreadable, not UTF-8 or without a closing fence is a fault,
            # with the path in vox.log alone.
            await reply.reject_or_fault(exc)
            return
        await reply.send({"type": "worker_result", "result": result})
