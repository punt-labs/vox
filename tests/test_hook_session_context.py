"""Per-session projection keeps worker decisions and newest text in sequence order."""

from __future__ import annotations

import pytest

from punt_vox.voxd.hook_session_context import SessionContext


def test_empty_session_has_no_decision_input_goal_or_result() -> None:
    session = SessionContext()
    snapshot = session.snapshot()
    assert snapshot["pending_decision"] is None
    assert snapshot["prompt_epoch"] == 0
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
    assert session.snapshot()["prompt_epoch"] == 3
    session.ingest(5, "Stop", {"last_assistant_message": "new"}, worker_input=True)
    session.ingest(4, "Stop", {"last_assistant_message": "old"}, worker_input=True)
    snapshot = session.snapshot()
    assert snapshot["session_started"]
    assert snapshot["goal"] == "task"
    assert snapshot["last_assistant_message"] == "new"
    assert snapshot["pending_decision"] is None
    assert snapshot["prompt_epoch"] == 5
    assert session.last_input() == (2, "task")
    assert session.inputs_since(1) == [(2, "task")]
    assert session.decisions.receipts(3) == []


def test_non_worker_notifications_do_not_create_worker_decisions() -> None:
    session = SessionContext()
    session.ingest(1, "Notification", {"notification_type": "permission_prompt"})
    session.ingest(3, "Stop", {})
    assert session.snapshot()["pending_decision"] is None
    assert session.snapshot()["prompt_epoch"] == 0


def test_invalid_zero_sequence_is_rejected() -> None:
    with pytest.raises(ValueError, match="expired"):
        SessionContext().ingest(0, "Stop", {})


def _tool(session: SessionContext, seq: int, event: str, **fields: object) -> None:
    payload: dict[str, object] = {
        "tool_name": "Bash",
        "tool_input": {"command": "make test"},
        **fields,
    }
    session.ingest(seq, event, payload, worker_input=True)


def test_a_tool_is_in_flight_until_its_completion_arrives() -> None:
    session = SessionContext()
    assert session.snapshot()["in_flight_tool"] is None
    _tool(session, 1, "PreToolUse")
    assert session.snapshot()["in_flight_tool"] == "Bash"
    _tool(session, 2, "PostToolUse", tool_response="ok")
    assert session.snapshot()["in_flight_tool"] is None


def test_a_failed_tool_is_no_longer_in_flight_and_stays_an_open_failure() -> None:
    session = SessionContext()
    _tool(session, 1, "PreToolUse")
    _tool(session, 2, "PostToolUseFailure", error="exit 2")
    snapshot = session.snapshot()
    assert snapshot["in_flight_tool"] is None
    assert snapshot["open_failure"] == {
        "relay_seq": 2,
        "tool": "Bash",
        "result": "exit 2",
    }


def test_the_newest_unfinished_tool_is_the_one_in_flight() -> None:
    session = SessionContext()
    _tool(session, 1, "PreToolUse")
    session.ingest(
        2,
        "PreToolUse",
        {"tool_name": "Edit", "tool_input": {"file_path": "/repo/a.py"}},
        worker_input=True,
    )
    assert session.snapshot()["in_flight_tool"] == "Edit"


def test_last_event_time_follows_worker_events_only() -> None:
    session = SessionContext()
    assert session.snapshot()["last_event_at"] == 0.0
    session.ingest(1, "SessionStart", {}, worker_input=True, at=50.0)
    session.ingest(2, "Stop", {}, worker_input=False, at=90.0)
    assert session.snapshot()["last_event_at"] == 50.0
    session.ingest(3, "Stop", {}, worker_input=True, at=70.0)
    assert session.snapshot()["last_event_at"] == 70.0


def test_in_flight_tool_and_event_time_survive_a_checkpoint_restore() -> None:
    session = SessionContext()
    session.ingest(1, "SessionStart", {}, worker_input=True, at=5.0)
    _tool(session, 2, "PreToolUse")
    restored = SessionContext()
    restored.restore(session.checkpoint())
    snapshot = restored.snapshot()
    assert snapshot["in_flight_tool"] == "Bash"
    assert snapshot["last_event_at"] == 5.0


def test_a_retried_old_hook_does_not_reset_the_silence_clock() -> None:
    session = SessionContext()
    session.ingest(1, "SessionStart", {}, worker_input=True, at=50.0)
    session.ingest(1, "SessionStart", {}, worker_input=True, at=90.0)
    assert session.snapshot()["last_event_at"] == 50.0


def test_a_tool_begun_before_the_newest_prompt_is_not_in_flight() -> None:
    session = SessionContext()
    _tool(session, 1, "PreToolUse")
    assert session.snapshot()["in_flight_tool"] == "Bash"
    session.ingest(2, "UserPromptSubmit", {"prompt": "never mind"}, worker_input=True)
    assert session.snapshot()["in_flight_tool"] is None


def test_a_tool_begun_after_the_newest_prompt_is_in_flight() -> None:
    session = SessionContext()
    session.ingest(1, "UserPromptSubmit", {"prompt": "go"}, worker_input=True)
    _tool(session, 2, "PreToolUse")
    assert session.snapshot()["in_flight_tool"] == "Bash"


def test_the_newest_prompt_or_tool_event_is_reported_as_the_turn_sequence() -> None:
    session = SessionContext()
    assert session.snapshot()["turn_seq"] == 0
    session.ingest(1, "SessionStart", {}, worker_input=True)
    assert session.snapshot()["turn_seq"] == 0
    session.ingest(2, "UserPromptSubmit", {"prompt": "go"}, worker_input=True)
    assert session.snapshot()["turn_seq"] == 2
    _tool(session, 3, "PostToolUse")
    session.ingest(4, "Stop", {}, worker_input=True)
    assert session.snapshot()["turn_seq"] == 3


def test_a_restart_keeps_a_tool_before_the_newest_prompt_out_of_flight() -> None:
    session = SessionContext()
    _tool(session, 1, "PreToolUse")
    session.ingest(2, "UserPromptSubmit", {"prompt": "never mind"}, worker_input=True)
    restored = SessionContext()
    restored.restore(session.checkpoint())
    assert session.snapshot()["in_flight_tool"] is None
    assert restored.snapshot() == session.snapshot()


def test_a_prompt_with_no_text_still_interrupts_the_tool_before_it() -> None:
    session = SessionContext()
    _tool(session, 1, "PreToolUse")
    session.ingest(2, "UserPromptSubmit", {"prompt": ""}, worker_input=True)
    restored = SessionContext()
    restored.restore(session.checkpoint())
    assert session.snapshot()["in_flight_tool"] is None
    assert restored.snapshot() == session.snapshot()


def test_the_prompt_boundary_survives_a_checkpoint_as_its_own_sequence() -> None:
    session = SessionContext()
    session.ingest(4, "UserPromptSubmit", {"prompt": ""}, worker_input=True)
    assert session.checkpoint()["prompt_seq"] == 4
