"""The ``program_status`` wire handler -- the authoritative status surface.

Reads the daemon's live :class:`ProgramStatus` on every call and returns it,
never a cached copy: a client asking "what is playing?" gets exactly what the
daemon holds, so no server-side shadow can drift. Reading a
log is not a strategy for a client -- both failure surfaces (program-level and
per-Part) cross the wire here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, final

if TYPE_CHECKING:
    from starlette.websockets import WebSocket

    from punt_vox.voxd.programs.service import ProgramService

__all__ = ["StatusHandler"]


@final
class StatusHandler:
    """Handle ``program_status``: reply with the daemon's authoritative status."""

    __slots__ = ("_service",)
    _service: ProgramService

    def __new__(cls, service: ProgramService) -> Self:
        self = super().__new__(cls)
        self._service = service
        return self

    async def __call__(self, msg: dict[str, object], websocket: WebSocket) -> None:
        """Reply with the current Program status, read fresh."""
        await websocket.send_json(
            {
                "type": "program_status",
                "id": str(msg.get("id", "")),
                "status": self._service.status().to_dict(),
            }
        )
