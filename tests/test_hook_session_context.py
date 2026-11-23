"""Per-session projection keeps worker decisions and newest text in sequence order."""

from __future__ import annotations

import pytest

from punt_vox.voxd.hook_session_context import SessionContext


def test_empty_session_has_no_decision_input_goal_or_result() -> None:
    session = SessionContext()
    snapshot = session.snapshot()
    assert snapshot["pending_decision"] is None
    assert snapshot["goal"] is None
    assert snapshot["last_assistant_message"] is None
    assert snapshot["active_files"] == []
    assert session.last_input() == (0, "")
    assert session.inputs_since(0) == []


def test_worker_decision_and_input_are_independent_of_latest_result() -> None:
    session = SessionContext()
    session.ingest(1, "SessionStart", {}, worker_input=True)
    session.ingest(2, "UserPromptSubmit", {"prompt": "task"}, worker_input=True)
    session.ingest(
        3,
        "Notification",
        {"notification_type": "idle_prompt", "message": "Next?"},
        worker_input=True,
    )
    assert session.snapshot()["pending_decision"] == {
        "notification_type": "idle_prompt",
        "message": "Next?",
        "seq": 3,
    }
    session.ingest(5, "Stop", {"last_assistant_message": "new"}, worker_input=True)
    session.ingest(4, "Stop", {"last_assistant_message": "old"}, worker_input=True)
    snapshot = session.snapshot()
    assert snapshot["session_started"]
    assert snapshot["goal"] == "task"
    assert snapshot["last_assistant_message"] == "new"
    assert snapshot["pending_decision"] is None
    assert session.last_input() == (2, "task")
    assert session.inputs_since(1) == [(2, "task")]
    assert session.decisions.receipts(3) == []


def test_non_worker_notifications_do_not_create_worker_decisions() -> None:
    session = SessionContext()
    session.ingest(1, "Notification", {"notification_type": "permission_prompt"})
    assert session.snapshot()["pending_decision"] is None


def test_invalid_zero_sequence_is_rejected() -> None:
    with pytest.raises(ValueError, match="expired"):
        SessionContext().ingest(0, "Stop", {})
