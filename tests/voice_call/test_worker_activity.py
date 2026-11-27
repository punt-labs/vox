"""A worker's spoken state and activity come from its checkpointed hook report."""

from __future__ import annotations

import pytest

from punt_vox.voxd.voice_call.worker_activity import WorkerActivity
from punt_vox.voxd.workers.model import WorkerState

from .fakes import payload

_PERMISSION = {"notification_type": "permission_prompt", "message": "ok?", "seq": 5}
_IDLE = {"notification_type": "idle_prompt", "message": "Next?", "seq": 5}


@pytest.mark.parametrize(
    ("report", "word"),
    [
        ({}, "idle"),
        ({"session_started": True, "worker_seq": 1, "prompt_epoch": 0}, "working"),
        ({"in_flight_tool": "Bash", "worker_seq": 4, "prompt_epoch": 0}, "working"),
        (
            {"pending_decision": _PERMISSION, "worker_seq": 5, "prompt_epoch": 5},
            "blocked",
        ),
        ({"pending_decision": _IDLE, "worker_seq": 5, "prompt_epoch": 5}, "idle"),
        ({"pending_decision": None, "worker_seq": 6, "prompt_epoch": 6}, "finished"),
    ],
)
def test_state_word_from_the_hook_report(report: dict[str, object], word: str) -> None:
    worker = payload("w")
    worker["last_report"] = report
    assert WorkerActivity(worker).state == word


@pytest.mark.parametrize("state", ["exited", "stopped"])
def test_a_worker_that_is_gone_is_finished(state: WorkerState) -> None:
    worker = payload("w", in_flight_tool="Bash")
    worker["state"] = state
    assert WorkerActivity(worker).state == "finished"


def test_detail_names_tool_files_failure_and_silence() -> None:
    worker = payload(
        "w",
        in_flight_tool="Edit",
        active_files=["/repo/a.py", "/repo/b.py"],
        open_failure={"relay_seq": 3, "tool": "Bash", "result": "exit 2"},
        last_event_at=100.0,
    )
    assert WorkerActivity(worker).detail(now=107.4) == {
        "tool_in_flight": "Edit",
        "files": ["/repo/a.py", "/repo/b.py"],
        "open_failure": {"tool": "Bash", "result": "exit 2"},
        "seconds_since_last_event": 7,
    }


def test_detail_omits_what_voxd_has_not_seen() -> None:
    assert WorkerActivity(payload("w")).detail(now=500.0) == {"files": []}


def test_clock_skew_never_reports_negative_silence() -> None:
    worker = payload("w", last_event_at=100.0)
    assert WorkerActivity(worker).detail(now=90.0)["seconds_since_last_event"] == 0
