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


def test_the_command_only_needs_the_vox_call_cli() -> None:
    assert _allowed_tools() == ["Bash(vox call:*)"]
