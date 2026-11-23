"""Decision state is ordered, detached from snapshots, and bounded."""

from __future__ import annotations

from punt_vox.voxd.hook_decisions import HookDecisions


def test_empty_state_and_irrelevant_notification() -> None:
    state = HookDecisions()
    assert state.pending() is None
    assert state.baseline() == 0
    assert state.latest == 0
    assert state.prompt_epoch == 0
    assert state.receipts(0) == []
    state.ingest(1, "Notification", {"notification_type": "other"})
    assert state.pending() is None
    assert state.prompt_epoch == 0


def test_prompt_epoch_survives_input_acknowledgement_and_ignores_stale_hooks() -> None:
    state = HookDecisions()
    state.ingest(2, "Notification", {"notification_type": "idle_prompt"})
    assert state.prompt_epoch == 2
    state.ingest(3, "UserPromptSubmit", {"prompt": "Continue."})
    assert state.pending() is None
    assert state.prompt_epoch == 2
    state.ingest(4, "PostToolUse", {})
    state.ingest(5, "Notification", {"notification_type": "other"})
    state.ingest(3, "Stop", {})
    assert state.prompt_epoch == 2
    state.ingest(6, "Stop", {})
    assert state.prompt_epoch == 6
    state.ingest(8, "Notification", {"notification_type": "permission_prompt"})
    assert state.prompt_epoch == 8


def test_pending_snapshot_is_detached_and_next_hook_acknowledges_it() -> None:
    state = HookDecisions()
    state.ingest(
        3,
        "Notification",
        {"notification_type": "permission_prompt", "message": "Allow?"},
    )
    pending = state.pending()
    assert pending is not None
    pending["message"] = "changed"
    assert state.pending() == {
        "notification_type": "permission_prompt",
        "message": "Allow?",
        "seq": 3,
    }
    assert state.baseline() == 3
    state.ingest(2, "Stop", {})
    assert state.baseline() == 3
    state.ingest(4, "PostToolUse", {})
    assert state.pending() is None
    assert state.latest == 4
    assert state.baseline() == 0
    assert state.receipts(3) == [4]
    assert state.receipts(2) == []


def test_receipt_history_is_bounded_and_missing_messages_are_empty() -> None:
    state = HookDecisions()
    for sequence in range(2, 140, 2):
        state.ingest(
            sequence, "Notification", {"notification_type": "permission_prompt"}
        )
        assert state.pending() == {
            "notification_type": "permission_prompt",
            "message": "",
            "seq": sequence,
        }
        state.ingest(sequence + 1, "PostToolUse", {})
    assert state.receipts(2) == []
    assert state.receipts(138) == [139]
