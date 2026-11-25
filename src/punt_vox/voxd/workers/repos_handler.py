"""The ``worker_repos`` RPC: a read-only view of the repository allowlist."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Self, final

from punt_vox.voxd.wire_reply import WireReply

if TYPE_CHECKING:
    from starlette.websockets import WebSocket

    from punt_vox.voxd.types import MessageHandler
    from punt_vox.voxd.voice_call.contracts import ProjectAllowlist

__all__ = ["WorkerReposHandler"]


@final
class WorkerReposHandler:
    """Answer ``worker_repos`` from the policy, without touching any worker."""

    __slots__ = ("_projects",)
    _projects: ProjectAllowlist
    ROUTE: ClassVar[str] = "worker_repos"

    def __new__(cls, projects: ProjectAllowlist) -> Self:
        """Bind the allowlist the daemon enforces."""
        self = super().__new__(cls)
        self._projects = projects
        return self

    def handlers(self) -> dict[str, MessageHandler]:
        """Expose the one read-only route."""
        return {self.ROUTE: self.handle}

    async def handle(self, msg: dict[str, object], websocket: WebSocket) -> None:
        """Reply with the allowlisted paths, or the policy's parse error."""
        reply = WireReply(websocket, str(msg.get("id", "")))
        try:
            repos = list(self._projects.repos())
        except ValueError as exc:
            await reply.error(exc)
            return
        await reply.send({"type": "worker_result", "result": repos})
