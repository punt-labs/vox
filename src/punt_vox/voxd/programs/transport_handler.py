"""One fieldless wire adapter for next, prev, pause, and resume."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Self, final

from punt_vox.types_programs.control import CommandOutcome
from punt_vox.voxd.programs.command_handler import ProgramCommandHandler

if TYPE_CHECKING:
    from collections.abc import Callable

    from punt_vox.voxd.programs.service import ProgramService

__all__ = ["TransportHandler"]

_NOTHING_PLAYING: Final = CommandOutcome.rejected("Nothing playing.")


@final
class TransportHandler(ProgramCommandHandler):
    """Adapt fieldless playback controls to the same wire result."""

    __slots__ = ("_action", "_wire_type")
    _action: Callable[[], bool]

    def __new__(
        cls, service: ProgramService, *, wire_type: str, action: Callable[[], bool]
    ) -> Self:
        """Bind a command to its wire type and its service operation."""
        self = super().__new__(cls, service)
        self._wire_type = wire_type
        self._action = action
        return self

    @classmethod
    def handlers(cls, service: ProgramService) -> dict[str, TransportHandler]:
        """Build the handler table for fieldless transport commands."""
        return {
            wire_type: cls(service, wire_type=wire_type, action=action)
            for wire_type, action in (
                ("program_next", service.advance),
                ("program_prev", service.prev),
                ("program_pause", service.pause),
                ("program_resume", service.resume),
            )
        }

    # None preserves the bare wire acknowledgement for an accepted command.
    def _run(self, _msg: dict[str, object], /) -> CommandOutcome | None:
        """Issue the bound transport command."""
        return None if self._action() else _NOTHING_PLAYING
