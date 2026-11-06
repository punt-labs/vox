"""Tests for :class:`PiTurnReply`: a turn's records become its reply chunks."""

from __future__ import annotations

from punt_vox.voxd.conversation_mode.pi_rpc_record import PiRpcRecord
from punt_vox.voxd.conversation_mode.pi_turn_reply import GUARD_REFUSAL, PiTurnReply
from punt_vox.voxd.conversation_mode.reply import ReplyChunk

_RETRY = PiRpcRecord({"type": "auto_retry_start", "attempt": 1})


def _delta(text: str) -> PiRpcRecord:
    event = {"type": "text_delta", "delta": text}
    return PiRpcRecord({"type": "message_update", "assistantMessageEvent": event})


def test_reply_text_becomes_a_chunk() -> None:
    assert PiTurnReply().chunks(_delta("It ")) == (
        ReplyChunk(text="It ", is_final=False),
    )


def test_a_record_with_no_text_carries_no_chunk() -> None:
    assert PiTurnReply().chunks(PiRpcRecord({"type": "agent_start"})) == ()


def test_a_retry_after_text_retracts_it() -> None:
    reply = PiTurnReply()
    reply.chunks(_delta("I think "))
    assert [c.is_restart for c in reply.chunks(_RETRY)] == [True]


def test_a_retry_before_any_text_retracts_nothing() -> None:
    assert PiTurnReply().chunks(_RETRY) == ()


def test_the_refusal_follows_spoken_text_after_a_space() -> None:
    reply = PiTurnReply()
    reply.chunks(_delta("Let me look."))
    assert reply.refusal().text == f" {GUARD_REFUSAL}"


def test_the_refusal_stands_alone_when_nothing_was_said() -> None:
    assert PiTurnReply().refusal().text == GUARD_REFUSAL
