"""Tests for ``plugin/commands/call.md``: every tool ``/call`` uses is granted."""

from __future__ import annotations

import json
from pathlib import Path

_CALL_MD = Path(__file__).resolve().parent.parent / "plugin" / "commands" / "call.md"


def _allowed_tools() -> list[str]:
    frontmatter = _CALL_MD.read_text(encoding="utf-8").split("---")[1]
    line = next(
        line for line in frontmatter.splitlines() if line.startswith("allowed-tools:")
    )
    tools: list[str] = json.loads(line.removeprefix("allowed-tools:"))
    return tools


def test_the_snapshot_step_s_quarry_search_is_granted() -> None:
    """The start step searches quarry before writing the snapshot."""
    assert "quarry MCP `find` tool" in _CALL_MD.read_text(encoding="utf-8")
    assert "mcp__quarry__find" in _allowed_tools()


def test_only_the_live_quarry_tool_name_is_granted() -> None:
    """quarry's find is ``mcp__quarry__find``; the plugin-scoped name is retired."""
    assert [tool for tool in _allowed_tools() if "quarry" in tool] == [
        "mcp__quarry__find"
    ]


def test_the_cli_and_snapshot_reads_are_granted() -> None:
    assert {"Bash(vox call:*)", "Read"} <= set(_allowed_tools())
