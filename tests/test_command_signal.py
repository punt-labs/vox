"""Tests for CommandOutcome (src/punt_vox/command_signal.py)."""

from __future__ import annotations

from punt_vox.command_signal import CommandOutcome


def _signal(exit_code: int | None, stdout: str) -> str | None:
    return CommandOutcome(exit_code, stdout).signal()


class TestExitCodeAuthoritative:
    """A zero exit never yields a failure; a nonzero exit never yields a pass."""

    def test_success_with_incidental_failed_word_is_not_failure(self) -> None:
        # "failed" appears in prose, but the command exited 0.
        assert _signal(0, "the failed-login test suite is next") != "tests-fail"

    def test_success_with_incidental_error_word_is_not_failure(self) -> None:
        assert _signal(0, "edited client_errors.py") not in {
            "tests-fail",
            "lint-fail",
            "cmd-fail",
        }

    def test_zero_exit_unrecognized_is_none(self) -> None:
        assert _signal(0, "arbitrary chatter") is None

    def test_nonzero_exit_unrecognized_is_cmd_fail(self) -> None:
        assert _signal(1, "arbitrary chatter") == "cmd-fail"

    def test_nonzero_exit_never_a_pass(self) -> None:
        # Even though "2534 passed" is present, the command failed.
        assert _signal(1, "2534 passed, 1 failed") == "tests-fail"


class TestStructuredTokens:
    """Recognition anchors to whole numeric summary tokens, case-sensitively."""

    def test_tests_pass(self) -> None:
        assert _signal(0, "5 passed in 1.2s") == "tests-pass"

    def test_tests_fail_numeric(self) -> None:
        assert _signal(1, "==== 3 failed, 2 passed ====") == "tests-fail"

    def test_tests_fail_pytest_line(self) -> None:
        assert _signal(1, "FAILED tests/test_x.py::test_y") == "tests-fail"

    def test_lint_fail(self) -> None:
        assert _signal(1, "Found 7 errors") == "lint-fail"

    def test_lint_pass(self) -> None:
        assert _signal(0, "0 errors, 0 warnings") == "lint-pass"

    def test_git_commit(self) -> None:
        assert _signal(0, "[main abc1234] fix: thing") == "git-commit"

    def test_pr_created(self) -> None:
        assert _signal(0, "https://github.com/o/r/pull/9") == "pr-created"

    def test_uppercase_error_word_not_matched(self) -> None:
        # IGNORECASE is gone: a stray "ERROR" banner is not lint-fail.
        assert _signal(1, "ERROR banner text") == "cmd-fail"


class TestTailScan:
    """The verdict at the end of a long run must be seen."""

    def test_summary_past_first_500_chars(self) -> None:
        out = ("." * 2000) + "\n==== 42 passed in 3s ===="
        assert _signal(0, out) == "tests-pass"

    def test_only_the_tail_is_scanned(self) -> None:
        # A marker at the start, buried well past the 4000-char tail cap by
        # trailing filler, is not seen.
        out = "12 passed\n" + ("x" * 6000)
        assert _signal(0, out) is None


class TestNoExitCode:
    """The transcript watcher passes text with no status code."""

    def test_none_pass(self) -> None:
        assert _signal(None, "5 passed in 1.2s") == "tests-pass"

    def test_none_fail(self) -> None:
        assert _signal(None, "FAILED tests/test_x.py") == "tests-fail"

    def test_none_bare_assertion_is_not_failure(self) -> None:
        assert _signal(None, "AssertionError: boom") is None

    def test_none_unrecognized_is_none_not_cmd_fail(self) -> None:
        assert _signal(None, "arbitrary chatter") is None


class TestSignalNames:
    def test_names_are_the_marker_signals(self) -> None:
        assert CommandOutcome.signal_names() == frozenset(
            {
                "tests-pass",
                "tests-fail",
                "lint-pass",
                "lint-fail",
                "git-push-ok",
                "git-commit",
                "pr-created",
                "merge-conflict",
            }
        )

    def test_cmd_fail_is_not_a_marker(self) -> None:
        assert "cmd-fail" not in CommandOutcome.signal_names()
