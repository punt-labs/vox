"""Tests for :mod:`punt_vox.voxd.voice_call.log_text`."""

from __future__ import annotations

import pytest

from punt_vox.voxd.voice_call.log_text import LogText


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("plain words", "plain words"),
        ("not allowlisted: /Users/x/repo", "not allowlisted: <path>"),
        ("allowlisted: ['/Users/x/a', '/srv/b']", "allowlisted: ['<path>', '<path>']"),
        ("[Errno 2] No such file: '/tmp/t.pcm'", "[Errno 2] No such file: '<path>'"),
        ("first\nsecond\r\nthird", "first second third"),
        ("a\u2028b", "a b"),
        ("half 1/2 and a.b/c", "half 1/2 and a.b/c"),
    ],
)
def test_reason_strips_line_breaks_and_absolute_paths(text: str, expected: str) -> None:
    assert LogText.reason(text) == expected


@pytest.mark.parametrize("value", ["headset", "aec", "none"])
def test_echo_control_keeps_an_accepted_declaration(value: str) -> None:
    assert LogText.echo_control(value) == value


@pytest.mark.parametrize("value", ["speakers", "head\nset", True, None, 3])
def test_echo_control_replaces_anything_else(value: object) -> None:
    assert LogText.echo_control(value) == "other"


def test_tool_name_keeps_a_catalog_tool() -> None:
    assert LogText.tool_name("start_worker") == "start_worker"


@pytest.mark.parametrize("name", ["no_such_tool", "x\nCall ended: forged", ""])
def test_tool_name_replaces_anything_else(name: str) -> None:
    assert LogText.tool_name(name) == "unknown"
