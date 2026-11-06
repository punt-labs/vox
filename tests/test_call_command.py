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


def test_both_quarry_find_tool_names_are_granted() -> None:
    """A standalone quarry server and a plugin install name the tool differently."""
    quarry = [tool for tool in _allowed_tools() if "quarry" in tool]
    assert sorted(quarry) == ["mcp__plugin_quarry_quarry__find", "mcp__quarry__find"]


def test_the_cli_and_snapshot_reads_are_granted() -> None:
    assert {"Bash(vox call:*)", "Read"} <= set(_allowed_tools())
