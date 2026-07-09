"""The ``program_on`` wire handler -- turn a Program on from authored prompts."""

from __future__ import annotations

from typing import final

from punt_vox.music_prompts import PromptSet
from punt_vox.voxd.programs.command_handler import ProgramCommandHandler

__all__ = ["OnHandler"]


@final
class OnHandler(ProgramCommandHandler):
    """Handle ``program_on``: bind or mint an album by tags/name and start it."""

    __slots__ = ()
    _WIRE_TYPE = "program_on"

    def _run(self, msg: dict[str, object], /) -> None:
        """Parse style/vibe/name/prompts and turn the Program on."""
        self._service.turn_on(
            style=self._opt_str(msg, "style"),
            vibe=self._opt_str(msg, "vibe"),
            name=self._opt_str(msg, "name"),
            prompts=PromptSet.from_wire(msg),
        )
