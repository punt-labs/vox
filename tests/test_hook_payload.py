"""Tests for the typed hook payloads in src/punt_vox/hook_payload.py."""

from __future__ import annotations

from pathlib import Path

from punt_vox.hook_payload import (
    BashPayload,
    NotificationPayload,
    StopPayload,
)

# ---------------------------------------------------------------------------
# StopPayload
# ---------------------------------------------------------------------------


class TestStopPayload:
    def test_stop_hook_active_true(self) -> None:
        result = StopPayload.parse({"stop_hook_active": True})
        assert result.stop_hook_active is True

    def test_stop_hook_active_false(self) -> None:
        result = StopPayload.parse({"stop_hook_active": False})
        assert result.stop_hook_active is False

    def test_stop_hook_active_missing_defaults_false(self) -> None:
        result = StopPayload.parse({})
        assert result.stop_hook_active is False

    def test_stop_hook_active_truthy_string_rejected(self) -> None:
        # "false" is truthy under bool() — must be rejected under the narrowed check
        result = StopPayload.parse({"stop_hook_active": "false"})
        assert result.stop_hook_active is False

    def test_stop_hook_active_integer_one_accepted(self) -> None:
        result = StopPayload.parse({"stop_hook_active": 1})
        assert result.stop_hook_active is True

    def test_stop_hook_active_integer_zero_rejected(self) -> None:
        result = StopPayload.parse({"stop_hook_active": 0})
        assert result.stop_hook_active is False


# ---------------------------------------------------------------------------
# BashPayload
# ---------------------------------------------------------------------------


class TestBashPayload:
    def test_tool_response_as_dict(self) -> None:
        # Claude Code emits the exit code under the camelCase key ``exitCode``.
        data: dict[str, object] = {"tool_response": {"exitCode": 0, "stdout": "hello"}}
        result = BashPayload.parse(data)
        assert result.exit_code == 0
        assert result.stdout == "hello"

    def test_snake_case_exit_code_is_ignored(self) -> None:
        # The real payload key is ``exitCode``; a snake_case ``exit_code`` is
        # not Claude Code's schema and must not be read.
        result = BashPayload.parse({"tool_response": {"exit_code": 0}})
        assert result.exit_code is None

    def test_tool_response_non_dict_treated_as_empty(self) -> None:
        result = BashPayload.parse({"tool_response": "oops"})
        assert result.exit_code is None
        assert result.stdout == ""

    def test_tool_response_absent(self) -> None:
        result = BashPayload.parse({})
        assert result.exit_code is None
        assert result.stdout == ""

    def test_exit_code_as_int(self) -> None:
        result = BashPayload.parse({"tool_response": {"exitCode": 1}})
        assert result.exit_code == 1

    def test_exit_code_as_string_int(self) -> None:
        result = BashPayload.parse({"tool_response": {"exitCode": "42"}})
        assert result.exit_code == 42

    def test_exit_code_non_numeric_string_becomes_none(self) -> None:
        result = BashPayload.parse({"tool_response": {"exitCode": "boom"}})
        assert result.exit_code is None

    def test_exit_code_absent_becomes_none(self) -> None:
        result = BashPayload.parse({"tool_response": {}})
        assert result.exit_code is None

    def test_stdout_absent_defaults_to_empty_string(self) -> None:
        result = BashPayload.parse({"tool_response": {"exitCode": 0}})
        assert result.stdout == ""


# ---------------------------------------------------------------------------
# NotificationPayload
# ---------------------------------------------------------------------------


class TestNotificationPayload:
    def test_notification_type_and_message_as_strings(self) -> None:
        data: dict[str, object] = {
            "notification_type": "permission_prompt",
            "message": "Needs your attention",
        }
        result = NotificationPayload.parse(data)
        assert result.notification_type == "permission_prompt"
        assert result.message == "Needs your attention"

    def test_notification_type_non_string_defaults(self) -> None:
        result = NotificationPayload.parse({"notification_type": 99, "message": "hi"})
        assert result.notification_type == "unknown"

    def test_message_non_string_defaults(self) -> None:
        result = NotificationPayload.parse(
            {"notification_type": "idle_prompt", "message": ["oops"]}
        )
        assert result.message == "Needs your attention"

    def test_notification_type_absent_defaults(self) -> None:
        result = NotificationPayload.parse({"message": "hi"})
        assert result.notification_type == "unknown"

    def test_message_absent_defaults(self) -> None:
        result = NotificationPayload.parse({"notification_type": "permission_prompt"})
        assert result.message == "Needs your attention"


# ---------------------------------------------------------------------------
# cwd carried on every payload kind
# ---------------------------------------------------------------------------


class TestPayloadCwd:
    """The session cwd is parsed onto every payload kind uniformly."""

    def test_stop_cwd_present(self) -> None:
        result = StopPayload.parse({"cwd": "/Users/me/Coding/punt-labs/vox"})
        assert result.cwd == Path("/Users/me/Coding/punt-labs/vox")

    def test_bash_cwd_present(self) -> None:
        result = BashPayload.parse({"cwd": "/Users/me/Coding/punt-labs/vox"})
        assert result.cwd == Path("/Users/me/Coding/punt-labs/vox")

    def test_notification_cwd_present(self) -> None:
        result = NotificationPayload.parse({"cwd": "/Users/me/Coding/punt-labs/vox"})
        assert result.cwd == Path("/Users/me/Coding/punt-labs/vox")

    def test_stop_cwd_absent_is_none(self) -> None:
        assert StopPayload.parse({}).cwd is None

    def test_bash_cwd_absent_is_none(self) -> None:
        assert BashPayload.parse({}).cwd is None

    def test_notification_cwd_absent_is_none(self) -> None:
        assert NotificationPayload.parse({}).cwd is None

    def test_cwd_non_string_is_none(self) -> None:
        # A non-string cwd (e.g. an int) is malformed — None, not a guess.
        assert StopPayload.parse({"cwd": 123}).cwd is None

    def test_cwd_empty_string_is_none(self) -> None:
        assert StopPayload.parse({"cwd": ""}).cwd is None
