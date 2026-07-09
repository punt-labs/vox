"""The ``program_list`` wire handler -- the album catalogue surface."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, final

if TYPE_CHECKING:
    from starlette.websockets import WebSocket

    from punt_vox.voxd.programs.catalog import Album
    from punt_vox.voxd.programs.service import ProgramService

__all__ = ["ListHandler"]


@final
class ListHandler:
    """Handle ``program_list``: reply with each catalog album's tags and counts."""

    __slots__ = ("_service",)
    _service: ProgramService

    def __new__(cls, service: ProgramService) -> Self:
        self = super().__new__(cls)
        self._service = service
        return self

    async def __call__(self, msg: dict[str, object], websocket: WebSocket) -> None:
        """Reply with each catalog album's id, tags, part counts, and timestamp."""
        rows = [self._row(album) for album in self._service.catalog_albums()]
        await websocket.send_json(
            {
                "type": "program_list",
                "id": str(msg.get("id", "")),
                "programs": rows,
            }
        )

    @staticmethod
    def _row(album: Album) -> dict[str, object]:
        """Return the catalogue row for one album (id, tags, counts, created)."""
        manifest = album.manifest
        tags = manifest.tags
        return {
            "id": manifest.id.value,
            "format": manifest.format.label,
            "style": tags.style,
            "vibe": tags.vibe,
            "name": tags.name,
            "ready": len(manifest.ready_parts()),
            "total": len(manifest.parts),
            "created": manifest.created.isoformat(),
        }
