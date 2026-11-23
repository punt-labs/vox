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
    assert reports.observe("worker", payload, None, latest=2) == WorkerProgress(
        "worker", 2, "finished", "done"
    )
    assert reports.observe("worker", payload, None, latest=2) is None
    assert reports.observe("other", payload, None, latest=2) == WorkerProgress(
        "other", 2, "finished", "done"
    )


def test_notification_requires_the_matching_current_decision() -> None:
    payload: dict[str, object] = {"relay_seq": 3, "hook_event_name": "Notification"}
    assert HookReports().observe("worker", payload, None, latest=3) is None
    reports = HookReports()
    assert (
        reports.observe(
            "worker",
            payload,
            {"notification_type": "permission_prompt", "message": "new", "seq": 4},
            latest=4,
        )
        is None
    )
    assert HookReports().observe(
        "worker",
        payload,
        {"notification_type": "idle_prompt", "message": "Next?", "seq": 3},
        latest=3,
    ) == WorkerProgress("worker", 3, "needs_decision", "Next?", "idle_prompt")


def test_missing_stop_text_is_empty_and_irrelevant_event_is_ignored() -> None:
    reports = HookReports()
    assert (
        reports.observe(
            "worker",
            {"relay_seq": 1, "hook_event_name": "SessionStart"},
            None,
            latest=1,
        )
        is None
    )
    assert reports.observe(
        "worker", {"relay_seq": 2, "hook_event_name": "Stop"}, None, latest=2
    ) == WorkerProgress("worker", 2, "finished", "")


def test_invalid_sequence_is_rejected() -> None:
    with pytest.raises(ValueError, match="expired"):
        HookReports().observe("worker", {"relay_seq": 0}, None, latest=0)


def test_stop_superseded_by_newer_store_state_is_not_published() -> None:
    assert (
        HookReports().observe(
            "worker",
            {
                "relay_seq": 3,
                "hook_event_name": "Stop",
                "last_assistant_message": "obsolete",
            },
            None,
            latest=4,
        )
        is None
    )
