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
