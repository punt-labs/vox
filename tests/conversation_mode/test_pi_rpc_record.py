"""Tests for :class:`PiRpcRecord`, against record shapes from a live pi run."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from punt_vox.voxd.conversation_mode.pi_rpc_record import (
    MalformedRecordError,
    PiRpcRecord,
)
from punt_vox.voxd.conversation_mode.secret_path_policy import SecretPathPolicy


def _record(fields: dict[str, object]) -> PiRpcRecord:
    return PiRpcRecord.parse(json.dumps(fields).encode())


def _update(event: dict[str, object]) -> PiRpcRecord:
    return _record({"type": "message_update", "assistantMessageEvent": event})


@pytest.fixture
def policy(tmp_path: Path) -> SecretPathPolicy:
    return SecretPathPolicy(tmp_path)


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


class TestTouches:
    def test_a_toolcall_end_reading_env_touches_the_deny_list(
        self, policy: SecretPathPolicy
    ) -> None:
        record = _update(
            {
                "type": "toolcall_end",
                "toolCall": {"name": "read", "arguments": {"path": ".env"}},
            }
        )
        assert record.touches(policy)

    def test_a_tool_execution_start_reading_env_touches_the_deny_list(
        self, policy: SecretPathPolicy
    ) -> None:
        record = _record(
            {
                "type": "tool_execution_start",
                "toolName": "read",
                "args": {"path": ".tmp/fake_secret.env"},
            }
        )
        assert record.touches(policy)

    def test_a_grep_glob_aimed_at_env_files_touches_the_deny_list(
        self, policy: SecretPathPolicy
    ) -> None:
        record = _record(
            {
                "type": "tool_execution_start",
                "toolName": "grep",
                "args": {"pattern": "API_KEY", "glob": "**/.env*"},
            }
        )
        assert record.touches(policy)

    def test_a_broad_grep_whose_result_lands_in_env_touches_the_deny_list(
        self, policy: SecretPathPolicy
    ) -> None:
        record = _record(
            {
                "type": "tool_execution_end",
                "toolName": "grep",
                "result": {
                    "content": [{"type": "text", "text": "config/.env:3: KEY=x"}]
                },
            }
        )
        assert record.touches(policy)

    def test_reading_source_does_not_touch_the_deny_list(
        self, policy: SecretPathPolicy
    ) -> None:
        record = _record(
            {
                "type": "tool_execution_start",
                "toolName": "read",
                "args": {"path": "pyproject.toml"},
            }
        )
        assert not record.touches(policy)

    def test_a_find_result_listing_names_is_not_screened(
        self, policy: SecretPathPolicy
    ) -> None:
        """A name in a listing is not a disclosure; only grep's output has content."""
        record = _record(
            {
                "type": "tool_execution_end",
                "toolName": "find",
                "result": {"content": [{"type": "text", "text": ".env"}]},
            }
        )
        assert not record.touches(policy)
