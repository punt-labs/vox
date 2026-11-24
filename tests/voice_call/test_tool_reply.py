"""Tests for :mod:`punt_vox.voxd.voice_call.tool_reply`."""

from __future__ import annotations

import json

from punt_vox.voxd.voice_call.tool_reply import ToolReply


def test_ok_carries_the_wire_text_and_no_error() -> None:
    answer = ToolReply.ok('{"silent": true}')
    assert answer.result == '{"silent": true}'
    assert answer.error is None
    assert not answer.is_error


def test_failed_builds_the_agent_reply_and_keeps_the_message() -> None:
    answer = ToolReply.failed("task must be a non-empty string")
    assert json.loads(answer.result) == {"error": "task must be a non-empty string"}
    assert answer.error == "task must be a non-empty string"
    assert answer.is_error


def test_failed_keeps_non_ascii_text_readable_on_the_wire() -> None:
    assert ToolReply.failed("café").result == '{"error": "café"}'
