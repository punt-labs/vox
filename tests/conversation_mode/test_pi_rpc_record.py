"""Tests for :class:`PiRpcRecord`, against record shapes from a live pi run."""

from __future__ import annotations

import json

import pytest

from punt_vox.voxd.conversation_mode.pi_rpc_record import (
    MalformedRecordError,
    PiRpcRecord,
)


def _record(fields: dict[str, object]) -> PiRpcRecord:
    return PiRpcRecord.parse(json.dumps(fields).encode())


def _update(event: dict[str, object]) -> PiRpcRecord:
    return _record({"type": "message_update", "assistantMessageEvent": event})


class TestParse:
    def test_a_non_json_line_is_malformed(self) -> None:
        with pytest.raises(MalformedRecordError):
            PiRpcRecord.parse(b"not json\n")

    def test_a_json_array_is_malformed(self) -> None:
        with pytest.raises(MalformedRecordError):
            PiRpcRecord.parse(b"[1, 2]\n")

    def test_an_unknown_record_type_is_inert(self) -> None:
        record = _record({"type": "queue_update", "steering": []})
        assert (record.is_settled, record.is_tool_event, record.text_delta()) == (
            False,
            False,
            "",
        )


class TestReplyText:
    def test_a_text_delta_carries_its_text(self) -> None:
        assert _update({"type": "text_delta", "delta": "punt"}).text_delta() == "punt"

    def test_a_thinking_delta_is_not_reply_text(self) -> None:
        assert _update({"type": "thinking_delta", "delta": "hmm"}).text_delta() == ""

    def test_agent_settled_ends_the_run(self) -> None:
        assert _record({"type": "agent_settled", "aborted": False}).is_settled


class TestToolEvents:
    @pytest.mark.parametrize(
        "record",
        [
            _update({"type": "toolcall_start", "toolName": "read"}),
            _update({"type": "toolcall_end", "toolCall": {"name": "read"}}),
            _record({"type": "tool_execution_start", "toolName": "read", "args": {}}),
            _record({"type": "tool_execution_end", "toolName": "read"}),
        ],
    )
    def test_tool_records_are_tool_events(self, record: PiRpcRecord) -> None:
        assert record.is_tool_event

    def test_a_text_delta_is_not_a_tool_event(self) -> None:
        assert not _update({"type": "text_delta", "delta": "x"}).is_tool_event


class TestFailure:
    def test_a_refused_command_is_a_failure(self) -> None:
        record = _record(
            {
                "type": "response",
                "command": "prompt",
                "success": False,
                "error": "Agent is already processing",
            }
        )
        assert record.failure() == "Agent is already processing"

    def test_an_accepted_command_is_not_a_failure(self) -> None:
        record = _record({"type": "response", "command": "prompt", "success": True})
        assert record.failure() == ""

    def test_an_errored_assistant_message_is_a_failure(self) -> None:
        record = _record(
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "stopReason": "error",
                    "errorMessage": "529 overloaded",
                },
            }
        )
        assert record.failure() == "529 overloaded"

    def test_a_normal_assistant_message_end_is_not_a_failure(self) -> None:
        record = _record(
            {
                "type": "message_end",
                "message": {"role": "assistant", "stopReason": "stop"},
            }
        )
        assert record.failure() == ""


class TestToolExecution:
    def test_a_start_record_carries_its_id_name_and_args(self) -> None:
        record = _record(
            {
                "type": "tool_execution_start",
                "toolCallId": "call_1",
                "toolName": "read",
                "args": {"path": "pyproject.toml"},
            }
        )
        assert (record.tool_call_id, record.tool_name, record.tool_args()) == (
            "call_1",
            "read",
            {"path": "pyproject.toml"},
        )

    def test_an_end_record_carries_its_text_result(self) -> None:
        record = _record(
            {
                "type": "tool_execution_end",
                "toolCallId": "call_1",
                "toolName": "grep",
                "isError": False,
                "result": {"content": [{"type": "text", "text": "a.py:3: x"}]},
            }
        )
        assert (record.is_error, record.result_text()) == (False, "a.py:3: x")

    def test_a_blocked_call_ends_as_an_error(self) -> None:
        record = _record(
            {"type": "tool_execution_end", "toolCallId": "c", "isError": True}
        )
        assert record.is_error

    def test_a_record_without_tool_fields_answers_empty(self) -> None:
        record = _record({"type": "agent_start"})
        assert (record.tool_call_id, record.tool_args(), record.result_text()) == (
            "",
            {},
            "",
        )
