"""An inherited configuration directory keeps its identity in a worktree."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from punt_vox.voxd.hook_context_store import HookContextStore
from punt_vox.voxd.workers.claude import ClaudeProfile

if TYPE_CHECKING:
    import pytest


def test_relative_inherited_claude_configuration_is_stable_across_worktrees(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "settings")
    (tmp_path / "settings").mkdir()
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    profile = ClaudeProfile(HookContextStore())
    environment = profile.environment(os.environ)
    assert environment["CLAUDE_CONFIG_DIR"] == str(tmp_path / "settings")
    monkeypatch.chdir(worktree)
    profile.prepare(worktree)
    assert (tmp_path / "settings" / ".claude.json").exists()
    assert not (worktree / "settings" / ".claude.json").exists()
    profile.cleanup(worktree)
