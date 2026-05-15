"""Tests for parse_hook_payload() in src/punt_vox/hook_payload.py."""

from __future__ import annotations

import pytest

from punt_vox.hook_payload import (
    BashPayload,
    NotificationPayload,
    StopPayload,
    parse_hook_payload,
)

# ---------------------------------------------------------------------------
# stop kind
# ---------------------------------------------------------------------------


class TestParseStopKind:
    def test_stop_hook_active_true(self) -> None:
        result = parse_hook_payload({"stop_hook_active": True}, "stop")
        assert isinstance(result, StopPayload)
        assert result.stop_hook_active is True

    def test_stop_hook_active_false(self) -> None:
        result = parse_hook_payload({"stop_hook_active": False}, "stop")
        assert isinstance(result, StopPayload)
        assert result.stop_hook_active is False

    def test_stop_hook_active_missing_defaults_false(self) -> None:
        result = parse_hook_payload({}, "stop")
        assert isinstance(result, StopPayload)
        assert result.stop_hook_active is False

    def test_stop_hook_active_truthy_string_rejected(self) -> None:
        # "false" is truthy under bool() — must be rejected under the narrowed check
        result = parse_hook_payload({"stop_hook_active": "false"}, "stop")
        assert isinstance(result, StopPayload)
        assert result.stop_hook_active is False

    def test_stop_hook_active_integer_one_accepted(self) -> None:
        result = parse_hook_payload({"stop_hook_active": 1}, "stop")
        assert isinstance(result, StopPayload)
        assert result.stop_hook_active is True

    def test_stop_hook_active_integer_zero_rejected(self) -> None:
        result = parse_hook_payload({"stop_hook_active": 0}, "stop")
        assert isinstance(result, StopPayload)
        assert result.stop_hook_active is False


# ---------------------------------------------------------------------------
# post_bash kind
# ---------------------------------------------------------------------------


class TestParsePostBashKind:
    def test_tool_response_as_dict(self) -> None:
        data: dict[str, object] = {"tool_response": {"exit_code": 0, "stdout": "hello"}}
        result = parse_hook_payload(data, "post_bash")
        assert isinstance(result, BashPayload)
        assert result.exit_code == 0
        assert result.stdout == "hello"

    def test_tool_response_non_dict_treated_as_empty(self) -> None:
        result = parse_hook_payload({"tool_response": "oops"}, "post_bash")
        assert isinstance(result, BashPayload)
        assert result.exit_code is None
        assert result.stdout == ""

    def test_tool_response_absent(self) -> None:
        result = parse_hook_payload({}, "post_bash")
        assert isinstance(result, BashPayload)
        assert result.exit_code is None
        assert result.stdout == ""

    def test_exit_code_as_int(self) -> None:
        result = parse_hook_payload({"tool_response": {"exit_code": 1}}, "post_bash")
        assert isinstance(result, BashPayload)
        assert result.exit_code == 1

    def test_exit_code_as_string_int(self) -> None:
        result = parse_hook_payload({"tool_response": {"exit_code": "42"}}, "post_bash")
        assert isinstance(result, BashPayload)
        assert result.exit_code == 42

    def test_exit_code_non_numeric_string_becomes_none(self) -> None:
        result = parse_hook_payload(
            {"tool_response": {"exit_code": "boom"}}, "post_bash"
        )
        assert isinstance(result, BashPayload)
        assert result.exit_code is None

    def test_exit_code_absent_becomes_none(self) -> None:
        result = parse_hook_payload({"tool_response": {}}, "post_bash")
        assert isinstance(result, BashPayload)
        assert result.exit_code is None

    def test_stdout_absent_defaults_to_empty_string(self) -> None:
        result = parse_hook_payload({"tool_response": {"exit_code": 0}}, "post_bash")
        assert isinstance(result, BashPayload)
        assert result.stdout == ""


# ---------------------------------------------------------------------------
# notification kind
# ---------------------------------------------------------------------------


class TestParseNotificationKind:
    def test_notification_type_and_message_as_strings(self) -> None:
        data: dict[str, object] = {
            "notification_type": "permission_prompt",
            "message": "Needs your attention",
        }
        result = parse_hook_payload(data, "notification")
        assert isinstance(result, NotificationPayload)
        assert result.notification_type == "permission_prompt"
        assert result.message == "Needs your attention"

    def test_notification_type_non_string_defaults(self) -> None:
        result = parse_hook_payload(
            {"notification_type": 99, "message": "hi"}, "notification"
        )
        assert isinstance(result, NotificationPayload)
        assert result.notification_type == "unknown"

    def test_message_non_string_defaults(self) -> None:
        result = parse_hook_payload(
            {"notification_type": "idle_prompt", "message": ["oops"]}, "notification"
        )
        assert isinstance(result, NotificationPayload)
        assert result.message == "Needs your attention"

    def test_notification_type_absent_defaults(self) -> None:
        result = parse_hook_payload({"message": "hi"}, "notification")
        assert isinstance(result, NotificationPayload)
        assert result.notification_type == "unknown"

    def test_message_absent_defaults(self) -> None:
        result = parse_hook_payload(
            {"notification_type": "permission_prompt"}, "notification"
        )
        assert isinstance(result, NotificationPayload)
        assert result.message == "Needs your attention"


# ---------------------------------------------------------------------------
# unknown kind
# ---------------------------------------------------------------------------


class TestParseUnknownKind:
    def test_unknown_kind_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unknown hook kind"):
            parse_hook_payload({}, "bogus")

    def test_empty_kind_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unknown hook kind"):
            parse_hook_payload({}, "")
