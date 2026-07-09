"""The ``program_select`` wire handler -- replay a Selection by tags/name/id.

Replaces the name-addressed play/loop handlers: a replay resolves either by a
direct ``id`` lookup (F#7 -- an id is *not* a tag axis) or by a
:class:`TagQuery` over ``style``/``vibe``/``name``, driving ``service.replay``.
The daemon animates the resulting Selection as a consume-only radio.
"""

from __future__ import annotations

from typing import final

from punt_vox.voxd.programs.album_id import AlbumId
from punt_vox.voxd.programs.album_tags import TagQuery
from punt_vox.voxd.programs.command_handler import ProgramCommandHandler

__all__ = ["SelectHandler"]


@final
class SelectHandler(ProgramCommandHandler):
    """Handle ``program_select``: replay by id (direct) or by a tag query."""

    __slots__ = ()
    _WIRE_TYPE = "program_select"

    def _run(self, msg: dict[str, object], /) -> None:
        """Route by resolution kind: a direct id lookup, else a tag query (F#7).

        The album id rides the wire as ``album_id`` -- distinct from the ``id``
        request-correlation field the envelope already uses.
        """
        album_id = self._opt_str(msg, "album_id")
        if album_id is not None:
            self._service.replay_album(AlbumId(album_id))
            return
        self._service.replay(
            TagQuery(
                style=self._opt_str(msg, "style"),
                vibe=self._opt_str(msg, "vibe"),
                name=self._opt_str(msg, "name"),
            )
        )
