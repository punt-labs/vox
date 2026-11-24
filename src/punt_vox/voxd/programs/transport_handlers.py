"""The transport ``program_*`` wire handlers: prev, pause, and resume.

Each is a thin adapter over :class:`ProgramService` in the same shape as the
``next``/``off`` handlers: parse nothing, issue the one serialized command, ack.
They give the ``mic:music`` / ``vox music`` transport verbs a daemon endpoint, so
each new capability has a non-UI caller alongside the in-scene transport buttons.
"""

from __future__ import annotations

from typing import Final, final

from punt_vox.types_programs.control import CommandOutcome
from punt_vox.voxd.programs.command_handler import ProgramCommandHandler

__all__ = ["PauseHandler", "PrevHandler", "ResumeHandler"]

_NOTHING_PLAYING: Final = CommandOutcome.rejected("Nothing playing.")


@final
class PrevHandler(ProgramCommandHandler):
    """Handle ``program_prev``: step the replay cursor back one part (Z ``Prev``)."""

    __slots__ = ()
    _WIRE_TYPE = "program_prev"

    def _run(self, _msg: dict[str, object], /) -> None:
        """Post the previous-part step (no fields to parse)."""
        self._service.prev()


@final
class PauseHandler(ProgramCommandHandler):
    """Handle ``program_pause``: suspend the active source in place (Z ``Pause``)."""

    __slots__ = ()
    _WIRE_TYPE = "program_pause"

    # None preserves the bare wire acknowledgement when pause succeeds.
    def _run(self, _msg: dict[str, object], /) -> CommandOutcome | None:
        """Suspend the active source (no fields to parse)."""
        return None if self._service.pause() else _NOTHING_PLAYING


@final
class ResumeHandler(ProgramCommandHandler):
    """Handle ``program_resume``: continue a suspended source (Z ``Resume``)."""

    __slots__ = ()
    _WIRE_TYPE = "program_resume"

    # None preserves the bare wire acknowledgement when resume succeeds.
    def _run(self, _msg: dict[str, object], /) -> CommandOutcome | None:
        """Continue the suspended source (no fields to parse)."""
        return None if self._service.resume() else _NOTHING_PLAYING
