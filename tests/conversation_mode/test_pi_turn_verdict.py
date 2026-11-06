"""Tests for :class:`PiTurnVerdict`: a turn's outcome is its final attempt's."""

from __future__ import annotations

import json
from collections.abc import Mapping

from punt_vox.voxd.conversation_mode.pi_rpc_record import PiRpcRecord
from punt_vox.voxd.conversation_mode.pi_turn_verdict import PiTurnVerdict


def _record(fields: Mapping[str, object]) -> PiRpcRecord:
    return PiRpcRecord.parse(json.dumps(fields).encode())


def _errored(message: str) -> PiRpcRecord:
    return _record(
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "stopReason": "error",
                "errorMessage": message,
            },
        }
    )


def _text(delta: str) -> PiRpcRecord:
    return _record(
        {
            "type": "message_update",
            "assistantMessageEvent": {"type": "text_delta", "delta": delta},
        }
    )


_RETRY = _record({"type": "auto_retry_start", "attempt": 1, "errorMessage": "529"})
_SETTLED = _record({"type": "agent_settled", "aborted": False})
_ABORTED = _record({"type": "agent_settled", "aborted": True})


def _verdict(*records: PiRpcRecord) -> PiTurnVerdict:
    verdict = PiTurnVerdict()
    for record in records:
        verdict.observe(record)
    return verdict


def test_a_clean_reply_has_no_failure() -> None:
    assert _verdict(_text("hi"), _SETTLED).failure == ""


def test_an_unretried_error_fails_the_turn() -> None:
    assert _verdict(_errored("529"), _SETTLED).failure == "529"


def test_a_retry_supersedes_the_failed_attempt() -> None:
    assert _verdict(_errored("529"), _RETRY, _text("hi"), _SETTLED).failure == ""


def test_an_attempt_failing_after_a_retry_fails_the_turn() -> None:
    verdict = _verdict(_errored("529"), _RETRY, _errored("500"), _SETTLED)
    assert verdict.failure == "500"


def test_exhausted_retries_fail_the_turn_with_the_final_error() -> None:
    exhausted = _record(
        {"type": "auto_retry_end", "success": False, "finalError": "gave up"}
    )
    assert _verdict(_errored("529"), exhausted, _SETTLED).failure == "gave up"


def test_an_aborted_run_with_no_text_is_a_failure() -> None:
    assert "aborted" in _verdict(_ABORTED).failure


def test_an_aborted_run_after_some_text_stands() -> None:
    assert _verdict(_text("partial"), _ABORTED).failure == ""


def test_a_guard_block_is_a_refusal() -> None:
    blocked = _record(
        {
            "type": "tool_execution_end",
            "toolCallId": "c1",
            "isError": True,
            "result": {"content": [{"type": "text", "text": "vox secret guard: no"}]},
        }
    )
    assert _verdict(blocked, _SETTLED).refused


def test_reply_text_means_the_turn_spoke() -> None:
    assert _verdict(_text("hi"), _SETTLED).spoke


def test_a_silent_turn_did_not_speak() -> None:
    assert not _verdict(_SETTLED).spoke
