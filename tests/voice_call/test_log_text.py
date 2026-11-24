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


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (
            RuntimeError("player missing at /opt/x"),
            "RuntimeError: player missing at <path>",
        ),
        (OSError("first\nsecond"), "OSError: first second"),
        (ValueError(), "ValueError"),
        (
            PermissionError(13, "Permission denied", "/Users/x/departure.json"),
            "PermissionError: [Errno 13] Permission denied: '<path>', errno 13",
        ),
        (
            FileNotFoundError(2, "No such file"),
            "FileNotFoundError: [Errno 2] No such file, errno 2",
        ),
    ],
)
def test_failure_names_an_exception_by_type_and_safe_message(
    exc: BaseException, expected: str
) -> None:
    assert LogText.failure(exc) == expected
