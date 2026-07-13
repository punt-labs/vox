"""Tests for the typed hook payloads in src/punt_vox/hook_payload.py."""

from __future__ import annotations

from pathlib import Path

from punt_vox.hook_payload import (
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

    def test_notification_cwd_present(self) -> None:
        result = NotificationPayload.parse({"cwd": "/Users/me/Coding/punt-labs/vox"})
        assert result.cwd == Path("/Users/me/Coding/punt-labs/vox")

    def test_stop_cwd_absent_is_none(self) -> None:
        assert StopPayload.parse({}).cwd is None

    def test_notification_cwd_absent_is_none(self) -> None:
        assert NotificationPayload.parse({}).cwd is None

    def test_cwd_non_string_is_none(self) -> None:
        # A non-string cwd (e.g. an int) is malformed — None, not a guess.
        assert StopPayload.parse({"cwd": 123}).cwd is None

    def test_cwd_empty_string_is_none(self) -> None:
        assert StopPayload.parse({"cwd": ""}).cwd is None
