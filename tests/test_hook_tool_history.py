"""Bounded tool history retains sender order and unresolved failures."""

from __future__ import annotations

from punt_vox.voxd.hook_tool_history import HookToolHistory


def test_empty_history() -> None:
    history = HookToolHistory()
    assert history.files_and_results() == ([], [])
    assert history.open_failure() is None


def test_event_cap_evicts_oldest_even_for_out_of_order_arrival() -> None:
    history = HookToolHistory()
    history.ingest(
        1, "PreToolUse", {"tool_name": "Read", "tool_input": {"file_path": "old"}}
    )
    history.ingest(
        3, "PreToolUse", {"tool_name": "Read", "tool_input": {"file_path": "boundary"}}
    )
    for sequence in range(50, 3, -1):
        history.ingest(sequence, "PreToolUse", {"tool_name": "Read", "tool_input": {}})
    assert history.files_and_results() == (["boundary"], [])
    history.ingest(
        2, "PreToolUse", {"tool_name": "Read", "tool_input": {"file_path": "also old"}}
    )
    assert history.files_and_results() == (["boundary"], [])
    history.ingest(51, "PreToolUse", {"tool_name": "Read", "tool_input": {}})
    assert history.files_and_results() == ([], [])


def test_files_results_limits_and_revisit_order() -> None:
    history = HookToolHistory()
    for sequence in range(1, 11):
        history.ingest(
            sequence,
            "PostToolUse",
            {
                "tool_name": "Read",
                "tool_input": {"file_path": f"file-{sequence}"},
                "tool_response": f"result-{sequence}",
            },
        )
    files, results = history.files_and_results()
    assert files == [f"file-{i}" for i in range(3, 11)]
    assert [r["relay_seq"] for r in results] == list(range(6, 11))
    history.ingest(
        11, "PreToolUse", {"tool_name": "Read", "tool_input": {"file_path": "file-3"}}
    )
    files, updated_results = history.files_and_results()
    assert files == [f"file-{i}" for i in range(4, 11)] + ["file-3"]
    assert updated_results == results


def test_open_failure_lookup_and_successful_retry() -> None:
    history = HookToolHistory()
    payload: dict[str, object] = {
        "tool_name": "Bash",
        "tool_input": {"command": "false"},
        "error": "failed",
    }
    history.ingest(1, "PostToolUseFailure", payload)
    assert history.open_failure() == {
        "relay_seq": 1,
        "tool": "Bash",
        "result": "failed",
    }
    history.ingest(3, "PostToolUse", {**payload, "tool_response": "recovered"})
    history.ingest(2, "PostToolUseFailure", payload)
    assert history.open_failure() is None


_BASH: dict[str, object] = {"tool_name": "Bash", "tool_input": {"command": "ls"}}


def test_a_tool_begun_before_the_boundary_is_not_in_flight() -> None:
    history = HookToolHistory()
    history.ingest(3, "PreToolUse", _BASH)
    assert history.in_flight(since=2) == "Bash"
    assert history.in_flight(since=3) is None
    assert history.in_flight(since=5) is None


def test_one_completion_settles_only_one_of_two_identical_parallel_calls() -> None:
    history = HookToolHistory()
    history.ingest(1, "PreToolUse", _BASH)
    history.ingest(2, "PreToolUse", _BASH)
    history.ingest(3, "PostToolUse", {**_BASH, "tool_response": "a"})
    assert history.in_flight(since=0) == "Bash"
    history.ingest(4, "PostToolUse", {**_BASH, "tool_response": "b"})
    assert history.in_flight(since=0) is None


def test_a_completion_that_arrives_first_still_settles_its_call() -> None:
    history = HookToolHistory()
    history.ingest(2, "PostToolUse", {**_BASH, "tool_response": "a"})
    history.ingest(1, "PreToolUse", _BASH)
    assert history.in_flight(since=0) is None


def test_the_newest_tool_sequence_is_zero_when_empty() -> None:
    history = HookToolHistory()
    assert history.newest() == 0
    history.ingest(7, "PreToolUse", _BASH)
    assert history.newest() == 7
