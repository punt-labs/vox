"""Tests for the call-agent errors: which end the call, and what is spoken."""

from __future__ import annotations

import pytest

from punt_vox.voxd.conversation_mode.call_agent_errors import (
    CallAgentEndedError,
    CallAgentSpawnError,
    SecretPathDeniedError,
)
from punt_vox.voxd.conversation_mode.session_attach import SessionAttachError

# What a real failure message can carry: pi's stderr, verbatim.
_STDERR = "call agent exited (exit 1): auth failed for sk-live-not-a-real-key"


@pytest.mark.parametrize(
    "error", [CallAgentEndedError, SecretPathDeniedError, CallAgentSpawnError]
)
def test_every_call_agent_error_is_a_session_attach_error(
    error: type[SessionAttachError],
) -> None:
    assert issubclass(error, SessionAttachError)


def test_a_secret_path_denial_ends_the_call() -> None:
    assert issubclass(SecretPathDeniedError, CallAgentEndedError)


def test_a_spawn_failure_does_not_claim_a_live_agent_ended() -> None:
    """It happens before the call opens, so it has no goodbye to speak."""
    assert not issubclass(CallAgentSpawnError, CallAgentEndedError)


@pytest.mark.parametrize("error", [CallAgentEndedError, SecretPathDeniedError])
def test_the_spoken_ending_never_carries_the_message(
    error: type[CallAgentEndedError],
) -> None:
    """The message can embed stderr; only the fixed sentence is read aloud."""
    phrase = error(_STDERR).ending_phrase
    assert ("sk-live" not in phrase, phrase.endswith("Ending the call now.")) == (
        True,
        True,
    )


def test_a_secret_path_denial_says_why_the_call_ends() -> None:
    assert "may hold secrets" in SecretPathDeniedError(_STDERR).ending_phrase


def test_an_ended_agent_says_it_stopped_responding() -> None:
    assert "stopped responding" in CallAgentEndedError(_STDERR).ending_phrase
