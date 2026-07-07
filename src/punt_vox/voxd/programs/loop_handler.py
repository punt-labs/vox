"""The ``program_loop`` wire handler -- replay a saved Program, rotating on end."""

from __future__ import annotations

from typing import final

from punt_vox.voxd.programs.command_handler import ProgramCommandHandler
from punt_vox.voxd.programs.identifiers import ProgramName

__all__ = ["LoopHandler"]


@final
class LoopHandler(ProgramCommandHandler):
    """Handle ``program_loop``: play a saved Program and rotate on every end."""

    __slots__ = ()
    _WIRE_TYPE = "program_loop"

    def _run(self, msg: dict[str, object], /) -> None:
        """Parse the name and start the looping replay."""
        name = msg.get("name")
        if not isinstance(name, str):
            raise_msg = "program_loop requires a name"
            raise ValueError(raise_msg)
        self._service.loop(ProgramName(name))
