"""``vox call stop``: hang up a live pi call, else voxd's Mode B call."""

from __future__ import annotations

from typing import Self, final

from punt_vox.commands.voice_call import VoiceCall
from punt_vox.voxd.conversation_mode.call_control import CallControl
from punt_vox.voxd.conversation_mode.call_lock import CallLock

__all__ = ["CallHangup"]


@final
class CallHangup:
    """The one stop verb for both call agents while the pi call still ships."""

    __slots__ = ()

    def __new__(cls) -> Self:
        """Create a hangup."""
        return super().__new__(cls)

    @staticmethod
    def stop() -> None:
        """Hang up the running call: a live pi call first, else voxd's call.

        A pi call is asked to hang up (FR-2's explicit end) through its
        mailbox; with none live, voxd's call is stopped over RPC.

        Reduces, but does not eliminate, the stale-mailbox race: never
        writes a pi request when no pi call is currently live, so a stop
        written against an already-dead lock no longer sits in the mailbox
        for the *next* call to silently consume on its first chunk. The check
        and the write are two separate filesystem operations in this process,
        uncoordinated with the live call's own ``lock.release()`` in a
        different process -- if the live call ends in the narrow window
        between them, the request still gets written and still lands in the
        next call's mailbox. A full fix needs
        the request tagged (pid/timestamp) and validated again on
        :meth:`~.call_control.CallControl.consume`.
        """
        # is_live(), not read() is not None: a crashed pi call leaves its lock
        # file behind with a dead pid, and a stop written to that mailbox
        # would be consumed by the *next* pi call instead.
        if CallLock.for_repo().is_live():
            CallControl.for_repo().request_stop()
        else:
            VoiceCall().stop()
