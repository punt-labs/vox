"""The failures that end a call agent, as distinct from a turn that merely failed.

A plain :class:`~.session_attach.SessionAttachError` is transient: the agent
process is still ``ready`` and the human can simply ask again. The errors
here mean the process is gone (``AbortTurn`` or an unexpected exit took it to
``ended``, which the call-agent Z model makes terminal), so every later turn
would fail the same way -- the call ends instead of inviting a doomed retry.
Each carries the fixed sentence spoken when it ends a call, never its own
message: that message can embed process stderr, which must not be read aloud.
"""

from __future__ import annotations

from punt_vox.voxd.conversation_mode.session_attach import SessionAttachError

__all__ = ["CallAgentEndedError", "CallAgentSpawnError", "SecretPathDeniedError"]


class CallAgentEndedError(SessionAttachError):
    """The call agent process ended mid-call; the call cannot continue."""

    @property
    def ending_phrase(self) -> str:
        """Return the fixed sentence spoken as this error ends the call."""
        return (
            "Sorry, the call agent stopped responding, so this call can't "
            "continue. Ending the call now."
        )


class SecretPathDeniedError(CallAgentEndedError):
    """The call agent reached for a path on the secret deny-list.

    Raised the moment the attempt is seen on the RPC stream; the turn is
    aborted and the process killed, so nothing the read may have returned is
    ever spoken, written to the summary, or kept in a live context.
    """

    @property
    def ending_phrase(self) -> str:
        return (
            "I can't open that file -- it may hold secrets, and anything I "
            "read on a call is spoken aloud. Ending the call now."
        )


class CallAgentSpawnError(SessionAttachError):
    """The call agent could not be started, so the call never opens."""
