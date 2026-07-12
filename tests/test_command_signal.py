"""Tests for CommandSignal (src/punt_vox/command_signal.py)."""

from __future__ import annotations

from punt_vox.command_signal import CommandSignal


def _signal(exit_code: int | None, stdout: str) -> str | None:
    return CommandSignal(exit_code, stdout).signal()


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


class TestMaskedFailure:
    """A structured failure verdict wins over a masking exit 0."""

    def test_piped_pytest_failure_still_fails(self) -> None:
        # `pytest | tee run.log` — exit is tee's (0), tail says it failed.
        assert _signal(0, "===== 2 failed, 3 passed in 1.2s =====") == "tests-fail"

    def test_zero_failed_summary_is_a_pass(self) -> None:
        # The critical edge: "0 failed" is not a failure. Success must win.
        assert _signal(0, "===== 0 failed, 5 passed in 1.2s =====") == "tests-pass"

    def test_make_test_or_true_failure_still_fails(self) -> None:
        # `make test || true` swallows the exit code; the verdict remains.
        assert _signal(0, "FAILED tests/test_x.py::test_y\nmake: done") == "tests-fail"

    def test_masked_lint_failure_still_fails(self) -> None:
        assert _signal(0, "Found 3 errors\n(exit swallowed by pipe)") == "lint-fail"

    def test_incidental_zero_failed_does_not_block_success(self) -> None:
        # A clean run reporting "0 failed" alongside "0 errors" is a pass.
        assert _signal(0, "0 failed, 0 errors, 12 passed") == "tests-pass"


class TestAnchoredGitMarkers:
    """git-push-ok and git-commit anchor to git's real output shapes."""

    def test_status_rename_with_main_substring_is_not_push(self) -> None:
        # "domain_v2" contains the substring "main"; a `git status` rename
        # line (exit 0, no push) must not read as a push.
        out = "renamed: src/domain.py -> src/domain_v2.py"
        assert _signal(0, out) != "git-push-ok"
        assert _signal(0, out) is None

    def test_rename_to_remaining_path_is_not_push(self) -> None:
        assert _signal(0, "        modules/remaining.py -> modules/kept.py") is None

    def test_real_push_ref_update_is_push(self) -> None:
        assert _signal(0, "   abc1234..def5678  main -> main") == "git-push-ok"

    def test_new_branch_push_is_push(self) -> None:
        assert _signal(0, " * [new branch]      feat -> feat") == "git-push-ok"

    def test_push_to_origin_ref_is_push(self) -> None:
        assert _signal(0, "abc123 -> origin/main") == "git-push-ok"

    def test_bracket_log_line_is_not_commit(self) -> None:
        # A bracket-prefixed log line is not a git commit summary.
        assert _signal(0, "[INFO] starting up") is None
        assert _signal(0, "[nodemon] restarting due to changes") is None
        assert _signal(0, "[2026-07-12 10:23] request handled") is None

    def test_real_commit_summary_is_commit(self) -> None:
        assert _signal(0, "[main a1b2c3d] feat: add thing") == "git-commit"

    def test_create_mode_line_is_commit(self) -> None:
        assert _signal(0, " create mode 100644 src/new.py\ncreate mode 100644 x")


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
        assert CommandSignal.signal_names() == frozenset(
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
        assert "cmd-fail" not in CommandSignal.signal_names()
