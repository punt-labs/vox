"""Tests for :class:`ReplyDelivery`: the ack cue, session-attach exchange,
redaction, and speech for one turn's reply.

Drives the real :class:`CallActor` (brought to ``waiting`` via
``StartCall``/``TurnDetected``, the same preconditions
:class:`~.call_session.CallSession` establishes before calling
:meth:`ReplyDelivery.deliver`) against the in-memory
:class:`FakeSessionAttach` -- no daemon, no subprocess.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from conversation_mode._session_attach_fakes import FakeSessionAttach, ScriptedChunk
from punt_vox.voxd.conversation_mode.call_actor import CallActor
from punt_vox.voxd.conversation_mode.mode import Mode
from punt_vox.voxd.conversation_mode.reply import ReplyChunk
from punt_vox.voxd.conversation_mode.reply_delivery import ReplyDelivery
from punt_vox.voxd.conversation_mode.start_call import StartCall
from punt_vox.voxd.conversation_mode.turn import TranscribedTurn
from punt_vox.voxd.conversation_mode.turn_detected import TurnDetected
from punt_vox.voxd.conversation_mode.turn_timer import LoggingTurnTimer

if TYPE_CHECKING:
    import pytest


class _Recorder:
    """A ``SpeakFn`` that records every phrase it was asked to speak."""

    def __init__(self) -> None:
        self.said: list[str] = []

    async def __call__(self, text: str) -> None:
        self.said.append(text)


def _actor_waiting() -> CallActor:
    """Return a :class:`CallActor` brought to ``waiting`` -- deliver's precondition."""
    actor = CallActor()
    actor.apply(StartCall())
    actor.apply(TurnDetected())
    return actor


async def test_deliver_speaks_an_ack_then_the_reply() -> None:
    speak = _Recorder()
    session_attach = FakeSessionAttach(
        (ScriptedChunk(ReplyChunk(text="It returns the sum.", is_final=True)),)
    )
    delivery = ReplyDelivery(
        session_attach=session_attach,
        speak=speak,
        actor=_actor_waiting(),
        turn_timer=LoggingTurnTimer(),
    )

    await delivery.deliver(TranscribedTurn(text="what does this do"))

    assert session_attach.turns() == ["what does this do"]
    assert "It returns the sum." in speak.said
    assert "Ready." in speak.said


async def test_deliver_returns_actor_to_listening() -> None:
    speak = _Recorder()
    session_attach = FakeSessionAttach(
        (ScriptedChunk(ReplyChunk(text="ok", is_final=True)),)
    )
    actor = _actor_waiting()
    delivery = ReplyDelivery(
        session_attach=session_attach,
        speak=speak,
        actor=actor,
        turn_timer=LoggingTurnTimer(),
    )

    await delivery.deliver(TranscribedTurn(text="hi"))

    assert actor.mode is Mode.LISTENING


async def test_ready_cue_is_spoken_while_still_in_speaking_mode() -> None:
    """Regression: ``ReplyEnds`` must apply after the "Ready." cue is spoken.

    ``ReplyEnds`` returns the call to ``LISTENING``, and
    ``CallSession.process_chunk`` accepts turn-detector input in both
    ``LISTENING`` and ``WAITING`` -- applying it before the cue risks a
    ``SpeakFn`` that doesn't gate the microphone treating "Ready." itself as
    human speech.
    """
    modes_when_spoken: list[Mode] = []

    async def speak(text: str) -> None:
        modes_when_spoken.append(actor.mode)
        _ = text

    session_attach = FakeSessionAttach(
        (ScriptedChunk(ReplyChunk(text="ok", is_final=True)),)
    )
    actor = _actor_waiting()
    delivery = ReplyDelivery(
        session_attach=session_attach,
        speak=speak,
        actor=actor,
        turn_timer=LoggingTurnTimer(),
    )

    await delivery.deliver(TranscribedTurn(text="hi"))

    # ack (before ReplyBegins), then the reply and "Ready." -- both spoken
    # while still SPEAKING, never after ReplyEnds returns to LISTENING.
    assert modes_when_spoken == [Mode.WAITING, Mode.SPEAKING, Mode.SPEAKING]
    assert actor.mode is Mode.LISTENING  # ReplyEnds applied only afterward


async def test_secret_shaped_reply_is_redacted_before_speaking_and_logged_verbatim(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(
        logging.INFO, logger="punt_vox.voxd.conversation_mode.reply_delivery"
    )
    secret = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789"
    speak = _Recorder()
    session_attach = FakeSessionAttach(
        (ScriptedChunk(ReplyChunk(text=f"The key is {secret}", is_final=True)),)
    )
    delivery = ReplyDelivery(
        session_attach=session_attach,
        speak=speak,
        actor=_actor_waiting(),
        turn_timer=LoggingTurnTimer(),
    )

    await delivery.deliver(TranscribedTurn(text="what's in the env file"))

    assert not any(secret in phrase for phrase in speak.said)
    assert any("[redacted]" in phrase for phrase in speak.said)
    assert any(secret in record.message for record in caplog.records)


def _restarted_reply() -> FakeSessionAttach:
    """A reply whose first attempt wrote text, then failed and was retried."""
    return FakeSessionAttach(
        (
            ScriptedChunk(ReplyChunk(text="I think the ", is_final=False)),
            ScriptedChunk(
                ReplyChunk(text="", is_final=False, retracts=len("I think the "))
            ),
            ScriptedChunk(ReplyChunk(text="It returns the sum.", is_final=True)),
        )
    )


async def test_a_restarted_reply_speaks_only_the_text_that_stands() -> None:
    """The reply is spoken once the turn settles: the retracted text was never heard."""
    speak = _Recorder()
    delivery = ReplyDelivery(
        session_attach=_restarted_reply(),
        speak=speak,
        actor=_actor_waiting(),
        turn_timer=LoggingTurnTimer(),
    )

    await delivery.deliver(TranscribedTurn(text="what does this do"))

    assert speak.said[1:] == ["It returns the sum.", "Ready."]


async def test_a_restarted_reply_never_speaks_the_failed_attempt() -> None:
    speak = _Recorder()
    delivery = ReplyDelivery(
        session_attach=_restarted_reply(),
        speak=speak,
        actor=_actor_waiting(),
        turn_timer=LoggingTurnTimer(),
    )

    await delivery.deliver(TranscribedTurn(text="what does this do"))

    assert not any("I think" in phrase for phrase in speak.said)


async def test_a_retry_that_says_nothing_new_speaks_no_apology() -> None:
    speak = _Recorder()
    retracted = len("I think the ")
    delivery = ReplyDelivery(
        session_attach=FakeSessionAttach(
            (
                ScriptedChunk(ReplyChunk(text="I think the ", is_final=False)),
                ScriptedChunk(ReplyChunk(text="", is_final=True, retracts=retracted)),
            )
        ),
        speak=speak,
        actor=_actor_waiting(),
        turn_timer=LoggingTurnTimer(),
    )

    await delivery.deliver(TranscribedTurn(text="what does this do"))

    assert speak.said[1:] == ["", "Ready."]
