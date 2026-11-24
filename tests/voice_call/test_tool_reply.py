"""Tests for :mod:`punt_vox.voxd.voice_call.tool_reply`."""

from __future__ import annotations

import json

import pytest

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


def test_from_wire_reads_the_error_once_from_the_runner_text() -> None:
    """The runner speaks {"error": ...}; from_wire is the one parse of it."""
    failed = ToolReply.from_wire('{"error": "name a project"}', is_error=True)
    assert failed == ToolReply('{"error": "name a project"}', "name a project")
    ok = ToolReply.from_wire('{"workers": []}', is_error=False)
    assert ok == ToolReply.ok('{"workers": []}')


@pytest.mark.parametrize("wire", ["{}", "[]", '{"error": 3}', "null", "not json"])
def test_from_wire_refuses_every_malformed_error_reply_with_one_value_error(
    wire: str,
) -> None:
    """Valid JSON of the wrong shape and invalid JSON fail the same way.

    The caller contains ValueError alone; a KeyError from a missing key or a
    TypeError from a list would escape it and end the call.
    """
    with pytest.raises(ValueError, match=r"not an error object|Expecting value"):
        ToolReply.from_wire(wire, is_error=True)
