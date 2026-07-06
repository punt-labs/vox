"""The ``program_on`` wire handler -- turn a Program on from authored prompts."""

from __future__ import annotations

from typing import final

from punt_vox.music_prompts import PromptSet
from punt_vox.voxd.programs.command_handler import ProgramCommandHandler

__all__ = ["OnHandler"]


@final
class OnHandler(ProgramCommandHandler):
    """Handle ``program_on``: create or resume a Program and start it."""

    __slots__ = ()
    _WIRE_TYPE = "program_on"

    def _run(self, msg: dict[str, object], /) -> None:
        """Parse style/name/prompts and turn the Program on."""
        self._service.turn_on(
            self._opt_str(msg, "style"),
            self._opt_str(msg, "name"),
            PromptSet.from_wire(msg),
        )

    @staticmethod
    def _opt_str(msg: dict[str, object], key: str) -> str | None:
        """Return a present string field, or ``None`` when absent (the contract)."""
        value = msg.get(key)
        return value if isinstance(value, str) else None
