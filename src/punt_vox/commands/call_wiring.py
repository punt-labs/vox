"""The collaborators every ``vox call`` drive loop shares, as one value.

Both drivers -- live (:mod:`~punt_vox.commands.call_live_driver`) and
scripted (:mod:`~punt_vox.commands.call_scripted`) -- differ only in where
audio and transcripts come from. Everything else a call needs is the same
for both and is decided once, by :mod:`~punt_vox.commands.call`, before
either driver is built: the call agent, the speech and chime channels, the
cross-process control mailbox, and the one turn timer the whole call marks
against (the call agent marks its own stages on it, so it must be the same
timer :class:`~punt_vox.voxd.conversation_mode.call_session.CallSession`
uses, or the two would keep separate clocks).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, final

if TYPE_CHECKING:
    from punt_vox.voxd.conversation_mode.call_control import ApplyControlFn, CallControl
    from punt_vox.voxd.conversation_mode.session_attach import SessionAttach
    from punt_vox.voxd.conversation_mode.speak_fn import SpeakFn
    from punt_vox.voxd.conversation_mode.turn_timer import TurnTimer
    from punt_vox.voxd.conversation_mode.wait_cue import ChimeFn

__all__ = ["CallWiring"]


@final
@dataclass(frozen=True, slots=True)
class CallWiring:
    """What a drive loop needs that does not depend on how audio arrives."""

    session_attach: SessionAttach
    speak: SpeakFn
    chime: ChimeFn
    control: CallControl
    apply_control: ApplyControlFn
    turn_timer: TurnTimer
