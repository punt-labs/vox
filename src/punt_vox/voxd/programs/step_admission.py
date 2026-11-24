"""Project an active playback source for FIFO step admission."""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.programs.lifecycle_signal import TurnOff, TurnOn
from punt_vox.voxd.programs.playback_signal import StepBack, StepForward
from punt_vox.voxd.programs.select_signal import SwitchSelection
from punt_vox.voxd.programs.switch_signal import SwitchProgram

if TYPE_CHECKING:
    from punt_vox.voxd.programs.control_signal import ControlSignal

__all__ = ["StepAdmission"]

_SOURCE_CHANGES = (SwitchProgram, SwitchSelection, TurnOn, TurnOff)


@final
class StepAdmission:
    """Keep only the start and stop projections of admitted commands."""

    __slots__ = ("_queued_active",)
    _queued_active: deque[bool]

    def __new__(cls) -> Self:
        """Start with no pending start or stop."""
        self = super().__new__(cls)
        self._queued_active = deque()
        return self

    def admit(self, signal: ControlSignal, *, active: bool) -> bool:
        """Record an admitted source change, or refuse a step projected off."""
        tail_active = self._queued_active[-1] if self._queued_active else active
        if isinstance(signal, (StepBack, StepForward)) and not tail_active:
            return False
        if isinstance(signal, _SOURCE_CHANGES):
            self._queued_active.append(not isinstance(signal, TurnOff))
        return True

    def consume(self, signal: ControlSignal) -> None:
        """Remove the oldest projection when its signal leaves the FIFO."""
        if isinstance(signal, _SOURCE_CHANGES):
            self._queued_active.popleft()
