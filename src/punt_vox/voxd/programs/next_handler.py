"""The ``program_next`` wire handler -- the one ungated advance transition."""

from __future__ import annotations

from typing import final

from punt_vox.voxd.programs.command_handler import ProgramCommandHandler

__all__ = ["NextHandler"]


@final
class NextHandler(ProgramCommandHandler):
    """Handle ``program_next``: advance to another Part (skip = next = loop)."""

    __slots__ = ()
    _WIRE_TYPE = "program_next"

    def _run(self, _msg: dict[str, object], /) -> None:
        """Post the advance (no fields to parse)."""
        self._service.advance()
