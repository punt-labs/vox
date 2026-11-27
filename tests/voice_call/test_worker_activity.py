"""A worker's spoken state and activity come from its checkpointed hook report."""

from __future__ import annotations

import pytest

from punt_vox.voxd.hook_session_context import SessionContext
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
        (
            {
                "pending_decision": _IDLE,
                "worker_seq": 5,
                "prompt_epoch": 5,
                "turn_seq": 3,
            },
            "finished",
        ),
        (
            {"worker_seq": 9, "prompt_epoch": 6, "turn_seq": 5},
            "finished",
        ),
        ({"worker_seq": 9, "prompt_epoch": 6, "turn_seq": 7}, "working"),
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


def test_a_clock_that_went_backwards_omits_the_silence() -> None:
    worker = payload("w", last_event_at=100.0)
    assert "seconds_since_last_event" not in WorkerActivity(worker).detail(now=90.0)


@pytest.mark.parametrize("error", ["launch_error", "stop_error", "cleanup_error"])
@pytest.mark.parametrize("lifecycle", ["starting", "ready"])
def test_a_worker_with_a_lifecycle_error_is_finished_never_idle(
    error: str, lifecycle: WorkerState
) -> None:
    worker = payload("w", **{error: "OSError"})
    worker["state"] = lifecycle
    assert WorkerActivity(worker).state == "finished"


def test_a_worker_still_starting_with_no_hook_is_idle() -> None:
    worker = payload("w")
    worker["state"] = "starting"
    assert WorkerActivity(worker).state == "idle"


def _state_after(*events: tuple[str, dict[str, object]]) -> str:
    context = SessionContext()
    for sequence, (event, fields) in enumerate(events, 1):
        context.ingest(sequence, event, fields, worker_input=True, at=float(sequence))
    worker = payload("w")
    worker["last_report"] = dict(context.checkpoint())
    return WorkerActivity(worker).state


_PROMPT = ("UserPromptSubmit", {"prompt": "go"})
_STOP = ("Stop", {"last_assistant_message": "done"})
_TOOL = ("PreToolUse", {"tool_name": "Bash", "tool_input": {"command": "ls"}})


@pytest.mark.parametrize(
    "later",
    [
        ("SessionEnd", {}),
        ("SubagentStop", {}),
        ("PreCompact", {}),
        ("Notification", {"notification_type": "auth_success"}),
        ("Notification", {"notification_type": "idle_prompt", "message": "Next?"}),
    ],
)
def test_a_finished_worker_stays_finished_through_non_work_events(
    later: tuple[str, dict[str, object]],
) -> None:
    assert _state_after(_PROMPT, _STOP, later) == "finished"


@pytest.mark.parametrize("next_work", [_PROMPT, _TOOL])
def test_a_new_prompt_or_tool_after_a_stop_is_working_again(
    next_work: tuple[str, dict[str, object]],
) -> None:
    assert _state_after(_PROMPT, _STOP, next_work) == "working"


def test_a_worker_working_before_any_stop_is_working() -> None:
    assert _state_after(_PROMPT) == "working"
