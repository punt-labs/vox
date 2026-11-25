"""Tests for :mod:`punt_vox.voxd.voice_call.transcript`."""

from __future__ import annotations

from punt_vox.voxd.voice_agent.events import (
    AgentResponse,
    AgentResponseCorrection,
    UserTranscript,
)
from punt_vox.voxd.voice_call.transcript import CallTranscript


def test_turns_are_kept_in_order_with_their_roles() -> None:
    transcript = CallTranscript()
    transcript.record(UserTranscript("please add a greeting", 1))
    transcript.record(AgentResponse("Starting a worker on that now.", 2, "r1"))
    assert transcript.turns() == [
        {"role": "user", "text": "please add a greeting"},
        {"role": "agent", "text": "Starting a worker on that now."},
    ]


def test_a_correction_rewrites_the_matching_agent_turn_in_place() -> None:
    transcript = CallTranscript()
    transcript.record(AgentResponse("the whole planned reply", 1, "r1"))
    transcript.record(UserTranscript("wait", 2))
    transcript.record(
        AgentResponseCorrection("the whole planned reply", "the", 3, "r1")
    )
    assert transcript.turns() == [
        {"role": "agent", "text": "the"},
        {"role": "user", "text": "wait"},
    ]


def test_a_correction_for_an_unknown_reply_changes_nothing() -> None:
    transcript = CallTranscript()
    transcript.record(AgentResponse("kept", 1, "r1"))
    transcript.record(AgentResponseCorrection("other", "cut", 2, "r9"))
    assert transcript.turns() == [{"role": "agent", "text": "kept"}]


def test_a_correction_picks_the_newest_turn_with_that_response_id() -> None:
    transcript = CallTranscript()
    transcript.record(AgentResponse("first", 1, "r1"))
    transcript.record(AgentResponse("second", 2, "r1"))
    transcript.record(AgentResponseCorrection("second", "sec", 3, "r1"))
    assert [turn["text"] for turn in transcript.turns()] == ["first", "sec"]


def test_only_the_last_twenty_turns_are_kept() -> None:
    transcript = CallTranscript()
    for n in range(25):
        transcript.record(UserTranscript(f"turn {n}", n))
    turns = transcript.turns()
    assert len(turns) == 20
    assert turns[0] == {"role": "user", "text": "turn 5"}
    assert turns[-1] == {"role": "user", "text": "turn 24"}


def test_turns_is_a_copy_the_caller_may_keep() -> None:
    transcript = CallTranscript()
    transcript.record(UserTranscript("hi", 1))
    first = transcript.turns()
    transcript.record(UserTranscript("again", 2))
    assert len(first) == 1
