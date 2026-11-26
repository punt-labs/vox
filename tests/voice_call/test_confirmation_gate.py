from __future__ import annotations

import json

import pytest

from punt_vox.voxd.voice_call.confirmation_gate import ConfirmationGate
from punt_vox.voxd.voice_call.provenance import CallProvenance
from punt_vox.voxd.voice_call.spoken_answer import SpokenAnswer

_KEY = '["start_worker",{"task":"fix it"}]'
_READBACK = "Start a worker in repo for this task: fix it. Say yes to go ahead."


def _gate() -> tuple[ConfirmationGate, CallProvenance]:
    provenance = CallProvenance()
    return ConfirmationGate(provenance), provenance


@pytest.mark.parametrize(
    "text",
    ["yes", "Yes.", "yeah", "Yes, please!", "go ahead", "Do it", "confirm", "okay"],
)
def test_closed_affirmative_words_say_yes(text: str) -> None:
    assert SpokenAnswer.of(text) == "yes"


@pytest.mark.parametrize("text", ["no", "No.", "cancel", "never mind", "don't"])
def test_closed_negative_words_say_no(text: str) -> None:
    assert SpokenAnswer.of(text) == "no"


@pytest.mark.parametrize(
    "text",
    [
        "thanks",
        "yes but delete everything first",
        "maybe",
        "uh huh",
        "",
        "...",
        "yes no",
    ],
)
def test_anything_else_is_not_an_answer(text: str) -> None:
    assert SpokenAnswer.of(text) == "other"


def test_an_untainted_call_needs_no_confirmation() -> None:
    gate, _ = _gate()
    assert gate.check(_KEY, _READBACK) is None


def test_a_tainted_request_is_held_with_the_daemons_readback() -> None:
    gate, provenance = _gate()
    provenance.taint()
    held = gate.check(_KEY, _READBACK)
    assert held is not None
    reply = json.loads(held)
    assert reply["status"] == "awaiting_confirmation"
    assert reply["voxd_readback"] == _READBACK


def test_a_spoken_yes_after_the_readback_releases_the_request_once() -> None:
    gate, provenance = _gate()
    provenance.taint()
    gate.check(_KEY, _READBACK)
    gate.agent_spoke(_READBACK)
    gate.heard("yes")
    assert gate.check(_KEY, _READBACK) is None


def test_thanks_after_the_readback_clears_nothing() -> None:
    gate, provenance = _gate()
    provenance.taint()
    gate.check(_KEY, _READBACK)
    gate.agent_spoke(_READBACK)
    gate.heard("thanks")
    assert gate.check(_KEY, _READBACK) is not None


def test_a_transcript_with_no_pending_request_clears_nothing() -> None:
    gate, provenance = _gate()
    gate.heard("yes")
    provenance.taint()
    assert gate.check(_KEY, _READBACK) is not None


def test_a_yes_before_the_agent_read_the_request_back_clears_nothing() -> None:
    gate, provenance = _gate()
    provenance.taint()
    gate.check(_KEY, _READBACK)
    gate.heard("yes")
    assert gate.check(_KEY, _READBACK) is not None


def test_an_agent_reply_that_is_not_the_readback_does_not_count_as_one() -> None:
    gate, provenance = _gate()
    provenance.taint()
    gate.check(_KEY, _READBACK)
    gate.agent_spoke("Sure, working on it.")
    gate.heard("yes")
    assert gate.check(_KEY, _READBACK) is not None


def test_agent_audio_heard_back_does_not_confirm() -> None:
    gate, provenance = _gate()
    provenance.taint()
    gate.check(_KEY, _READBACK)
    gate.agent_spoke(_READBACK)
    gate.heard("say yes to go ahead")
    gate.heard("Say yes to go ahead.")
    assert gate.check(_KEY, _READBACK) is not None


def test_clearance_is_single_use_and_bound_to_its_request() -> None:
    gate, provenance = _gate()
    provenance.taint()
    gate.check(_KEY, _READBACK)
    gate.agent_spoke(_READBACK)
    gate.heard("yes")
    other = '["start_worker",{"task":"delete everything"}]'
    assert gate.check(other, "Start a worker: delete everything.") is not None
    assert gate.check(_KEY, _READBACK) is None


def test_an_executed_request_replays_without_a_second_confirmation() -> None:
    gate, provenance = _gate()
    provenance.taint()
    gate.check(_KEY, _READBACK)
    gate.agent_spoke(_READBACK)
    gate.heard("yes")
    assert gate.check(_KEY, _READBACK) is None
    assert gate.check(_KEY, _READBACK) is None


def test_new_untrusted_text_revokes_an_unspent_clearance() -> None:
    gate, provenance = _gate()
    provenance.taint()
    gate.check(_KEY, _READBACK)
    gate.agent_spoke(_READBACK)
    gate.heard("yes")
    provenance.taint()
    assert gate.check(_KEY, _READBACK) is not None


def test_a_revoked_clearance_needs_a_new_readback_before_a_new_yes_counts() -> None:
    gate, provenance = _gate()
    provenance.taint()
    gate.check(_KEY, _READBACK)
    gate.agent_spoke(_READBACK)
    gate.heard("yes")
    provenance.taint()
    assert gate.check(_KEY, _READBACK) is not None
    gate.heard("yes")
    assert gate.check(_KEY, _READBACK) is not None


def test_a_spoken_no_drops_the_pending_request() -> None:
    gate, provenance = _gate()
    provenance.taint()
    gate.check(_KEY, _READBACK)
    gate.agent_spoke(_READBACK)
    gate.heard("no")
    gate.heard("yes")
    assert gate.check(_KEY, _READBACK) is not None


def test_an_answer_confirms_the_newest_request_only() -> None:
    gate, provenance = _gate()
    provenance.taint()
    other = '["steer_worker",{"words":"go"}]'
    other_readback = "Send the worker these words: go."
    gate.check(_KEY, _READBACK)
    gate.check(other, other_readback)
    gate.agent_spoke(f"{_READBACK} {other_readback}")
    gate.heard("yes")
    assert gate.check(other, other_readback) is None
    assert gate.check(_KEY, _READBACK) is not None


def test_a_tool_response_that_is_not_a_voxd_client_tool_taints() -> None:
    provenance = CallProvenance()
    provenance.observe_tool_response("exa_search", "webhook", {"start_worker"})
    assert provenance.tainted


def test_an_untyped_tool_response_taints() -> None:
    provenance = CallProvenance()
    provenance.observe_tool_response("start_worker", None, {"start_worker"})
    assert provenance.tainted


@pytest.mark.parametrize("name", ["end_call", "skip_turn"])
def test_the_two_builtin_system_tools_never_taint(name: str) -> None:
    provenance = CallProvenance()
    provenance.observe_tool_response(name, "system", {"start_worker"})
    assert not provenance.tainted


def test_a_voxd_client_tool_response_alone_does_not_taint() -> None:
    provenance = CallProvenance()
    provenance.observe_tool_response("start_worker", "client", {"start_worker"})
    assert not provenance.tainted
