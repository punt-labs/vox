"""What a tool answered becomes a trace note only when it is a real answer."""

from __future__ import annotations

import io
import json

import pytest

from punt_vox.voxd.voice_agent.events import ClientToolCall
from punt_vox.voxd.voice_agent.trace import EventTrace
from punt_vox.voxd.voice_call.tool_trace import ToolTrace


def _record(tool: str, result: str, **parameters: object) -> list[dict[str, object]]:
    stream = io.StringIO()
    ToolTrace(EventTrace(stream)).record(
        ClientToolCall("call-1", tool, parameters, expects_response=True), result
    )
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def test_a_started_worker_is_traced_by_id() -> None:
    rows = _record("start_worker", json.dumps({"worker_id": "abcd1234"}))
    assert [(r["type"], r["detail"]) for r in rows] == [
        ("worker_started", {"worker_id": "abcd1234"})
    ]


def test_a_start_held_for_a_spoken_yes_traces_no_worker() -> None:
    held = {"status": "awaiting_confirmation", "voxd_readback": "Start a worker?"}
    assert _record("start_worker", json.dumps(held)) == []


def test_a_start_with_no_worker_id_traces_no_worker() -> None:
    assert _record("start_worker", json.dumps({"worker_id": None})) == []
    assert _record("start_worker", json.dumps({"task": "x"})) == []


@pytest.mark.parametrize("tool", ["start_worker", "worker_status", "steer_worker"])
@pytest.mark.parametrize("result", ["[]", '"text"', "3", "null"])
def test_a_reply_that_is_not_an_object_traces_nothing_and_never_raises(
    tool: str, result: str
) -> None:
    assert _record(tool, result) == []


def test_other_tools_leave_no_note() -> None:
    assert _record("list_workers", "[]") == []
