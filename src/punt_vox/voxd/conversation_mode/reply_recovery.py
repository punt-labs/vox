"""Recovers a call from a mid-turn session-attach failure, per exception subtype.

Two kinds of failure reach :class:`~.call_session.CallSession` through the
same :class:`~.session_attach.SessionAttachError` hierarchy, but only one of
them can succeed on retry. A reply the provider failed, or a turn that could
not be forwarded, leaves the call agent ready: the call recovers to listening
and the human just tries again. A call agent that has ended
(:class:`~.call_agent_errors.CallAgentEndedError` -- it went silent, it died,
it reached for a secret) is gone for good, so every later turn would fail the
same way: that one ends the call instead of looping "try again" forever.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.conversation_mode.call_agent_errors import CallAgentEndedError
from punt_vox.voxd.conversation_mode.end_call import EndCall
from punt_vox.voxd.conversation_mode.reply_begins import ReplyBegins
from punt_vox.voxd.conversation_mode.reply_ends import ReplyEnds

if TYPE_CHECKING:
    from punt_vox.voxd.conversation_mode.call_actor import CallActor
    from punt_vox.voxd.conversation_mode.session_attach import SessionAttachError
    from punt_vox.voxd.conversation_mode.speak_fn import SpeakFn

__all__ = ["ReplyRecovery"]

logger = logging.getLogger(__name__)

# FR-19's low-confidence path never leaves listening for the turn it can't
# act on; a SessionAttachError happens after TurnDetected already moved the
# call to waiting (see CallState.turn_detected's precondition), so recovery
# has to speak its way back through speaking -> listening rather than
# staying put -- the apology IS the reply this turn gets.
_SESSION_ATTACH_FAILED = "Sorry, something went wrong -- go ahead and try again."


@final
class ReplyRecovery:
    """Speaks the right apology and applies the right transition for *exc*."""

    __slots__ = ("_actor", "_speak")
    _actor: CallActor
    _speak: SpeakFn

    def __new__(cls, actor: CallActor, speak: SpeakFn) -> Self:
        self = super().__new__(cls)
        self._actor = actor
        self._speak = speak
        return self

    async def recover(self, exc: SessionAttachError) -> None:
        """React to *exc*, raised while a turn's reply was in flight.

        Waiting has no direct path back to listening (``CallState`` has no
        such transition), so every recovery travels through speaking, same
        as a real reply would -- the apology (or, when the call agent has
        ended, the goodbye) IS the reply this turn gets. The goodbye is the
        error's own fixed sentence, never its message, which can carry the
        agent's stderr.
        """
        self._actor.apply(ReplyBegins())
        if isinstance(exc, CallAgentEndedError):
            # exc_info=exc, not logger.exception(): correct regardless of
            # whether the caller is inside the except block handling *exc*.
            logger.error("call agent ended mid-call", exc_info=exc)
            await self._speak(exc.ending_phrase)
            self._actor.apply(EndCall())
            return
        logger.error("session-attach failed mid-turn", exc_info=exc)
        await self._speak(_SESSION_ATTACH_FAILED)
        self._actor.apply(ReplyEnds())
