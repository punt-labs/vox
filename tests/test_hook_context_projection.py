"""Bounded hook context projections."""

from punt_vox.voxd.hook_context_projection import HookText, ToolUpdate


def test_shell_result_keeps_tail_without_exposing_tool_input() -> None:
    update = ToolUpdate.from_payload(
        {
            "tool_name": "Bash",
            "tool_input": {"command": "private line"},
            "tool_response": {"stdout": "x" * HookText.LIMIT, "stderr": "last line"},
        },
        "PostToolUse",
    )
    result = update.as_result(7)
    assert result["relay_seq"] == 7
    assert result["result"].startswith("...")
    assert result["result"].endswith("\nlast line")
    assert "private line" not in result["result"]
