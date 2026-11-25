"""Types for the album list worker and its wire rows."""

from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from punt_vox.voxd.programs.catalog import Album

__all__ = ["AlbumListRow", "ListRead"]


class AlbumListRow(TypedDict):
    """One album in a program_list reply."""

    id: str
    format: str
    style: str
    vibe: str
    name: str | None  # None means an unnamed, tag-addressed album.
    ready: int
    total: int
    created: str


type ListRead = tuple[Album, AlbumListRow | str]
