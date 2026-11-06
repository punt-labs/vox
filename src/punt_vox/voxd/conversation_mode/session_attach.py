"""The Protocol a live call uses to forward a turn to its call agent.

:class:`SessionAttach` is the interface :mod:`~punt_vox.voxd.conversation_mode`
calls to forward a transcribed human turn and receive the agent's reply as it
streams in -- FR-4's requirement that a call carry the user's task context
(as a context snapshot handed to a persistent call agent, not a literal
resume of the interactive session), and FR-11's requirement that speech
begin on the reply's first complete portion. Production backs it with one
``pi --mode rpc`` process held for the whole call
(:class:`~.pi_call_agent.PiCallAgent`); tests inject
:class:`~tests.conversation_mode._session_attach_fakes.FakeSessionAttach`. No
method takes a session identifier -- one :class:`SessionAttach` instance is
already bound to one call's agent for its lifetime.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from punt_vox.voxd.conversation_mode.reply import ReplyChunk
    from punt_vox.voxd.conversation_mode.turn import TranscribedTurn

__all__ = ["SessionAttach", "SessionAttachError"]


class SessionAttachError(RuntimeError):
    """A turn could not be forwarded, or its reply ended abnormally.

    A boundary error (PY-EH-1), raised where the call learns the turn failed,
    not caught and retried internally. This base class is the transient case
    -- the agent is still there and the human can simply ask again;
    :class:`~.call_agent_errors.CallAgentEndedError` is the permanent one.
    """


@runtime_checkable
class SessionAttach(Protocol):
    """One human turn in, one streamed agent reply out, for one call's agent."""

    def send_turn(self, turn: TranscribedTurn) -> AsyncIterator[ReplyChunk]:
        """Forward ``turn`` to the call agent and stream its reply back.

        Returns an async iterator, not an awaited value: the caller starts
        consuming chunks (and can start speaking, per FR-11) before the
        agent has finished composing the rest of the reply. Raises
        :class:`SessionAttachError` if the turn cannot be forwarded or its
        reply ends abnormally.
        """
        ...
