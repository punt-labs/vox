"""Checkpointed milestones publish once and never revive an obsolete decision."""

from __future__ import annotations

import pytest

from punt_vox.voxd.voice_call.progress_types import WorkerProgress
from punt_vox.voxd.workers.hook_reports import HookReports


def test_stop_publishes_once_per_worker_sequence() -> None:
    reports = HookReports()
    payload: dict[str, object] = {
        "relay_seq": 2,
        "hook_event_name": "Stop",
        "last_assistant_message": "done",
    }
    assert reports.observe("worker", payload, None) == WorkerProgress(
        "worker", 2, "finished", "done"
    )
    assert reports.observe("worker", payload, None) is None
    assert reports.observe("other", payload, None) == WorkerProgress(
        "other", 2, "finished", "done"
    )


def test_notification_requires_the_matching_current_decision() -> None:
    payload: dict[str, object] = {"relay_seq": 3, "hook_event_name": "Notification"}
    assert HookReports().observe("worker", payload, None) is None
    reports = HookReports()
    assert (
        reports.observe(
            "worker",
            payload,
            {"notification_type": "permission_prompt", "message": "new", "seq": 4},
        )
        is None
    )
    assert HookReports().observe(
        "worker",
        payload,
        {"notification_type": "idle_prompt", "message": "Next?", "seq": 3},
    ) == WorkerProgress("worker", 3, "needs_decision", "Next?", "idle_prompt")


def test_missing_stop_text_is_empty_and_irrelevant_event_is_ignored() -> None:
    reports = HookReports()
    assert (
        reports.observe(
            "worker", {"relay_seq": 1, "hook_event_name": "SessionStart"}, None
        )
        is None
    )
    assert reports.observe(
        "worker", {"relay_seq": 2, "hook_event_name": "Stop"}, None
    ) == WorkerProgress("worker", 2, "finished", "")


def test_invalid_sequence_is_rejected() -> None:
    with pytest.raises(ValueError, match="expired"):
        HookReports().observe("worker", {"relay_seq": 0}, None)
