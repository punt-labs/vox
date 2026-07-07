"""The ``program_list`` wire handler -- the saved-Program catalogue surface."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, final

if TYPE_CHECKING:
    from starlette.websockets import WebSocket

    from punt_vox.voxd.programs.manifest import ProgramManifest
    from punt_vox.voxd.programs.service import ProgramService

__all__ = ["ListHandler"]


@final
class ListHandler:
    """Handle ``program_list``: reply with every saved Program's part counts."""

    __slots__ = ("_service",)
    _service: ProgramService

    def __new__(cls, service: ProgramService) -> Self:
        self = super().__new__(cls)
        self._service = service
        return self

    async def __call__(self, msg: dict[str, object], websocket: WebSocket) -> None:
        """Reply with each saved Program's name, format label, and part counts."""
        await websocket.send_json(
            {
                "type": "program_list",
                "id": str(msg.get("id", "")),
                "programs": [self._summary(m) for m in self._service.saved_programs()],
            }
        )

    @staticmethod
    def _summary(manifest: ProgramManifest) -> dict[str, object]:
        """Return the catalogue row for one saved Program (name, label, counts)."""
        return {
            "name": manifest.name.value,
            "format": manifest.format.label,
            "ready": len(manifest.ready_parts()),
            "total": len(manifest.parts),
        }
