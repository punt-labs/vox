"""The daemon's one seam to the Programs domain -- service plus wire handlers.

``ProgramSubsystem`` is the composition seam the daemon holds instead of reaching
into a dozen program modules: it builds the :class:`ProgramService` from the
on-disk store and the ElevenLabs producer and hands out the seven ``program_*``
wire handlers bound to that service. Keeping the wiring here gives the daemon a
single import into the subsystem (PY-DP-10) and keeps the handler roster in
exactly one place.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.programs.filesystem_store import FilesystemProgramStore
from punt_vox.voxd.programs.list_handler import ListHandler
from punt_vox.voxd.programs.loop_handler import LoopHandler
from punt_vox.voxd.programs.next_handler import NextHandler
from punt_vox.voxd.programs.off_handler import OffHandler
from punt_vox.voxd.programs.on_handler import OnHandler
from punt_vox.voxd.programs.play_handler import PlayHandler
from punt_vox.voxd.programs.service import ProgramService
from punt_vox.voxd.programs.sleeper import RealSleeper
from punt_vox.voxd.programs.status_handler import StatusHandler

if TYPE_CHECKING:
    from pathlib import Path

    from punt_vox.voxd.programs.producer import Producer
    from punt_vox.voxd.types import MessageHandler

__all__ = ["ProgramSubsystem"]


@final
class ProgramSubsystem:
    """Own the one ProgramService and expose its wire handlers to the daemon."""

    __slots__ = ("_root", "_service")
    _root: Path
    _service: ProgramService

    def __new__(cls, root: Path, producer: Producer) -> Self:
        self = super().__new__(cls)
        self._root = root
        self._service = ProgramService(
            producer, FilesystemProgramStore(root), root, RealSleeper()
        )
        return self

    @property
    def service(self) -> ProgramService:
        """Return the service the daemon runs and the handlers drive."""
        return self._service

    def handlers(self) -> dict[str, MessageHandler]:
        """Return the seven ``program_*`` wire handlers bound to the service."""
        service = self._service
        return {
            "program_on": OnHandler(service),
            "program_off": OffHandler(service),
            "program_next": NextHandler(service),
            "program_play": PlayHandler(service),
            "program_loop": LoopHandler(service),
            "program_list": ListHandler(service),
            "program_status": StatusHandler(service),
        }
