"""Tests for :mod:`punt_vox.voxd.voice_call.call_log`."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import pytest

from punt_vox.voxd.voice_call.call_log import CallLog
from punt_vox.voxd.voice_call.call_outcome import CallOutcome
from punt_vox.voxd.voice_call.tool_reply import ToolReply

_LOGGER = "punt_vox.voxd.voice_call.call_log"
_TRACE = Path("/traces/call-abc.jsonl")


@pytest.fixture
def records(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.INFO, logger=_LOGGER)
    return caplog


def _messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.message for r in caplog.records if r.name == _LOGGER]


class TestStarting:
    def test_a_script_request_logs_the_script_input(
        self, records: pytest.LogCaptureFixture
    ) -> None:
        with CallLog.starting({"type": "call_start", "script": []}):
            pass
        assert _messages(records) == ["Call starting: input script, echo control none"]

    @pytest.mark.parametrize("echo_control", ["headset", "aec"])
    def test_a_mic_request_logs_its_echo_declaration(
        self, records: pytest.LogCaptureFixture, echo_control: str
    ) -> None:
        with CallLog.starting({"type": "call_start", "echo_control": echo_control}):
            pass
        assert _messages(records) == [
            f"Call starting: input mic, echo control {echo_control}"
        ]

    def test_a_mic_request_without_a_declaration_logs_none(
        self, records: pytest.LogCaptureFixture
    ) -> None:
        with CallLog.starting({"type": "call_start"}):
            pass
        assert _messages(records) == ["Call starting: input mic, echo control none"]


def test_live_logs_the_conversation_id(records: pytest.LogCaptureFixture) -> None:
    CallLog.live("conv_42")
    assert _messages(records) == ["Call live: conversation conv_42"]


class TestTool:
    def test_a_successful_tool_logs_ok(self, records: pytest.LogCaptureFixture) -> None:
        CallLog.tool("list_workers", ToolReply.ok(json.dumps({"workers": []})))
        assert _messages(records) == ["Call tool list_workers: ok"]

    def test_a_failed_tool_logs_the_error_text(
        self, records: pytest.LogCaptureFixture
    ) -> None:
        CallLog.tool(
            "start_worker", ToolReply.failed("task must be a non-empty string")
        )
        assert _messages(records) == [
            "Call tool start_worker: error task must be a non-empty string"
        ]

    @pytest.mark.parametrize("result", ["not json", '{"ok": true}'])
    def test_an_error_whose_wire_text_is_not_an_error_object_still_logs(
        self, records: pytest.LogCaptureFixture, result: str
    ) -> None:
        """The log never re-parses the wire text: the error travels structured."""
        CallLog.tool("start_worker", ToolReply(result, "task must be text"))
        assert _messages(records) == ["Call tool start_worker: error task must be text"]


class TestEnded:
    def test_leaving_the_context_logs_the_outcome_duration_and_trace(
        self, records: pytest.LogCaptureFixture
    ) -> None:
        with CallLog(_TRACE, lambda: CallOutcome("ended", None, 3)):
            pass
        (line,) = _messages(records)
        assert re.fullmatch(
            r"Call ended: ended, 3 tool calls, duration \d+\.\d s, trace "
            + re.escape(str(_TRACE)),
            line,
        )

    def test_one_tool_call_is_singular(self, records: pytest.LogCaptureFixture) -> None:
        with CallLog(_TRACE, lambda: CallOutcome("ended", None, 1)):
            pass
        (line,) = _messages(records)
        assert ", 1 tool call, duration" in line

    def test_a_failed_call_names_its_error(
        self, records: pytest.LogCaptureFixture
    ) -> None:
        outcome = CallOutcome("failed", "agent provisioning failed", 0)
        with CallLog(_TRACE, lambda: outcome):
            pass
        (line,) = _messages(records)
        assert line.startswith("Call ended: failed, 0 tool calls, duration ")
        assert line.endswith(f", error agent provisioning failed, trace {_TRACE}")

    def test_the_outcome_is_read_on_exit_not_on_entry(
        self, records: pytest.LogCaptureFixture
    ) -> None:
        outcomes = [CallOutcome("connecting", None, 0), CallOutcome("ended", None, 2)]
        with CallLog(_TRACE, lambda: outcomes[-1]):
            outcomes.pop()
        (line,) = _messages(records)
        assert line.startswith("Call ended: connecting, 0 tool calls")

    def test_an_exception_still_logs_the_end_and_propagates(
        self, records: pytest.LogCaptureFixture
    ) -> None:
        outcome = CallOutcome("failed", "boom", 0)
        with (
            pytest.raises(RuntimeError, match="boom"),
            CallLog(_TRACE, lambda: outcome),
        ):
            raise RuntimeError("boom")
        (line,) = _messages(records)
        assert line.startswith("Call ended: failed")
        assert ", error boom, " in line

    def test_the_context_returns_the_log_itself(self) -> None:
        log = CallLog(_TRACE, lambda: CallOutcome("ended", None, 0))
        with log as entered:
            assert entered is log


class TestSetup:
    def test_a_failing_setup_logs_the_exception_type_only_and_propagates(
        self, records: pytest.LogCaptureFixture
    ) -> None:
        with (
            pytest.raises(OSError, match="/Users/x/traces"),
            CallLog.starting({"type": "call_start", "script": []}),
        ):
            raise FileExistsError("[Errno 17] File exists: '/Users/x/traces'")
        assert _messages(records) == [
            "Call starting: input script, echo control none",
            "Call not started: FileExistsError",
        ]


class TestRedaction:
    """Client and worker text is never logged raw: no paths, no forged lines."""

    def test_an_unrecognized_echo_declaration_logs_other_on_one_line(
        self, records: pytest.LogCaptureFixture
    ) -> None:
        with CallLog.starting({"type": "call_start", "echo_control": "x\ny"}):
            pass
        assert _messages(records) == ["Call starting: input mic, echo control other"]

    def test_a_tool_error_logs_no_absolute_path(
        self, records: pytest.LogCaptureFixture
    ) -> None:
        answer = ToolReply.failed("not allowlisted in worker_repos: /Users/x/repo")
        CallLog.tool("start_worker", answer)
        (line,) = _messages(records)
        assert "/Users" not in line
        assert line == (
            "Call tool start_worker: error not allowlisted in worker_repos: <path>"
        )

    def test_a_tool_error_with_a_newline_stays_on_one_line(
        self, records: pytest.LogCaptureFixture
    ) -> None:
        CallLog.tool("start_worker", ToolReply.failed("first\nCall ended: forged"))
        assert _messages(records) == [
            "Call tool start_worker: error first Call ended: forged"
        ]

    def test_an_unknown_tool_name_with_a_newline_logs_unknown(
        self, records: pytest.LogCaptureFixture
    ) -> None:
        CallLog.tool("x\nCall ended: forged", ToolReply.ok(json.dumps({"ok": 1})))
        assert _messages(records) == ["Call tool unknown: ok"]

    def test_the_end_line_error_logs_no_absolute_path(
        self, records: pytest.LogCaptureFixture
    ) -> None:
        outcome = CallOutcome(
            "failed", "[Errno 13] Permission denied: '/Users/x/calls/a.jsonl'", 0
        )
        with CallLog(_TRACE, lambda: outcome):
            pass
        (line,) = _messages(records)
        assert "/Users" not in line
        assert ", error [Errno 13] Permission denied: '<path>', trace " in line
