"""The ``program_play`` wire handler -- cold-start replay of a saved Program."""

from __future__ import annotations

from typing import final

from punt_vox.voxd.programs.command_handler import ProgramCommandHandler
from punt_vox.voxd.programs.format import Format
from punt_vox.voxd.programs.identifiers import PartRef, ProgramName

__all__ = ["PlayHandler"]


@final
class PlayHandler(ProgramCommandHandler):
    """Handle ``program_play``: play a saved Program, optionally at a part index."""

    __slots__ = ()
    _WIRE_TYPE = "program_play"

    def _run(self, msg: dict[str, object], /) -> None:
        """Parse the name and optional 1-based part, then play from disk."""
        name = msg.get("name")
        if not isinstance(name, str):
            raise_msg = "program_play requires a name"
            raise ValueError(raise_msg)
        self._service.play(ProgramName(name), self._part(msg.get("part")))

    @staticmethod
    def _part(raw: object) -> PartRef | None:
        """Resolve the optional part index into a ``PartRef`` (playlist scope)."""
        if raw is None:
            return None
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise_msg = "part must be an integer"
            raise ValueError(raise_msg)
        return PartRef(Format.PLAYLIST, raw)
