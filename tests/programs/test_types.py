"""Tests for the album list wire schema."""

from __future__ import annotations

from typing import get_type_hints

from punt_vox.voxd.programs.types import AlbumListRow


def test_album_list_row_requires_all_wire_fields() -> None:
    expected = {
        "id": str,
        "format": str,
        "style": str,
        "vibe": str,
        "name": str | None,
        "ready": int,
        "total": int,
        "created": str,
    }
    assert AlbumListRow.__required_keys__ == expected.keys()
    assert get_type_hints(AlbumListRow) == expected
