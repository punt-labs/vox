"""A git failure is one line: type, argv and stderr, with every path scrubbed."""

from __future__ import annotations

import subprocess

import pytest

from punt_vox.voxd.workers.git_failure import GitFailure


def test_called_process_error_names_the_command_and_stderr() -> None:
    failure = GitFailure(
        subprocess.CalledProcessError(
            128,
            ["git", "-C", "/Users/x/repo", "branch", "-D", "vox-worker/a"],
            stderr="error: branch '/Users/x/repo' not found.\n",
        )
    )
    assert str(failure) == (
        "CalledProcessError: git -C <path> branch -D vox-worker/a: "
        "error: branch '<path>' not found."
    )


def test_timeout_names_the_command_without_stderr() -> None:
    failure = GitFailure(subprocess.TimeoutExpired(["git", "worktree", "prune"], 20))
    assert str(failure) == "TimeoutExpired: git worktree prune"


def test_bytes_stderr_is_decoded() -> None:
    failure = GitFailure(
        subprocess.CalledProcessError(1, ["git"], stderr=b"fatal: /tmp/x\xff")
    )
    assert failure.detail == "git: fatal: <path>"


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("[Errno 16] busy: '/Users/x/y'", "[Errno 16] busy: '<path>'"),
        (
            "not the worker's own worktree: /srv/w/1",
            "not the worker's own worktree: <path>",
        ),
        ("a 1.5 ratio and a/b relative path", "a 1.5 ratio and a/b relative path"),
    ],
)
def test_other_errors_keep_their_message_with_paths_scrubbed(
    message: str, expected: str
) -> None:
    assert GitFailure(OSError(message)).detail == expected


def test_kind_is_the_exception_class() -> None:
    assert GitFailure(ValueError("x")).kind == "ValueError"


def test_reason_is_the_first_stderr_line_scrubbed() -> None:
    failure = GitFailure(
        subprocess.CalledProcessError(
            128,
            ["git"],
            stderr="fatal: a branch named 'vox-worker/x' already exists\nhint: /x/y\n",
        )
    )
    assert failure.reason == "fatal: a branch named 'vox-worker/x' already exists"


def test_reason_without_stderr_names_the_exit_status() -> None:
    assert GitFailure(subprocess.CalledProcessError(128, ["git"])).reason == (
        "git exited 128"
    )


def test_reason_of_a_timeout_or_plain_error_is_its_detail() -> None:
    assert GitFailure(OSError("busy: /x")).reason == "busy: <path>"
    assert GitFailure(subprocess.TimeoutExpired(["git"], 9)).reason == "git"
