"""Tests for :class:`~.reply_recovery.ReplyRecovery`."""

from __future__ import annotations

import pytest

from punt_vox.voxd.conversation_mode.call_actor import CallActor
from punt_vox.voxd.conversation_mode.call_agent_errors import (
    CallAgentEndedError,
    SecretPathDeniedError,
)
from punt_vox.voxd.conversation_mode.mode import Mode
from punt_vox.voxd.conversation_mode.reply_recovery import ReplyRecovery
from punt_vox.voxd.conversation_mode.session_attach import SessionAttachError
from punt_vox.voxd.conversation_mode.start_call import StartCall
from punt_vox.voxd.conversation_mode.turn_detected import TurnDetected


class _Recorder:
    def __init__(self) -> None:
        self.said: list[str] = []

    async def __call__(self, text: str) -> None:
        self.said.append(text)


def _actor_waiting() -> CallActor:
    """Return a :class:`CallActor` already in ``waiting`` -- the mode every
    real mid-turn failure is raised from (see :meth:`ReplyRecovery.recover`'s
    own docstring for why)."""
    actor = CallActor()
    actor.apply(StartCall())
    actor.apply(TurnDetected())
    return actor


async def test_a_transient_session_attach_error_recovers_to_listening() -> None:
    speak = _Recorder()
    actor = _actor_waiting()
    recovery = ReplyRecovery(actor, speak)

    await recovery.recover(SessionAttachError("call agent reply failed: 529"))

    assert actor.mode is Mode.LISTENING
    assert any("wrong" in phrase.lower() for phrase in speak.said)


@pytest.mark.parametrize(
    "exc",
    [
        CallAgentEndedError("no event from the call agent in 60s"),
        SecretPathDeniedError("call agent reached for a denied path"),
    ],
)
async def test_an_ended_call_agent_ends_the_call(exc: CallAgentEndedError) -> None:
    speak = _Recorder()
    actor = _actor_waiting()
    recovery = ReplyRecovery(actor, speak)

    await recovery.recover(exc)

    assert actor.mode is Mode.IDLE


async def test_an_ended_call_agent_speaks_its_own_fixed_goodbye() -> None:
    speak = _Recorder()
    exc = CallAgentEndedError("call agent exited: Traceback ... sk-secret-in-stderr")

    await ReplyRecovery(_actor_waiting(), speak).recover(exc)

    assert speak.said == [exc.ending_phrase]


async def test_a_denied_path_explains_why_the_call_is_ending() -> None:
    speak = _Recorder()

    await ReplyRecovery(_actor_waiting(), speak).recover(
        SecretPathDeniedError("call agent reached for .env")
    )

    assert "secrets" in speak.said[0]


async def test_the_goodbye_never_leaks_the_raw_exception_text() -> None:
    """A fixed sentence, never {exc} itself -- an ended agent's message can
    embed its stderr, which must not be read aloud."""
    speak = _Recorder()
    exc = CallAgentEndedError("call agent exited: FAKE_API_KEY=sk-not-real")

    await ReplyRecovery(_actor_waiting(), speak).recover(exc)

    assert not any(str(exc) in phrase for phrase in speak.said)
