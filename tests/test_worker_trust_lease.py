"""Trust edits participate in Claude's native directory lease."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from punt_vox.atomic_file import AtomicFile
from punt_vox.voxd.workers.trust import ClaudeTrust


def test_trust_edit_refuses_a_live_native_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    config = tmp_path / ".claude.json"
    config.write_text('{"unrelated": true}')
    (tmp_path / ".claude.json.lock").mkdir()
    with patch("time.sleep"), pytest.raises(TimeoutError):
        ClaudeTrust().update(tmp_path / "worktree", accepted=True)
    assert json.loads(config.read_text()) == {"unrelated": True}


def test_failed_edit_releases_the_native_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    with (
        patch.object(AtomicFile, "replace", side_effect=OSError("disk")),
        pytest.raises(OSError),
    ):
        ClaudeTrust().update(tmp_path / "worktree", accepted=True)
    assert not (tmp_path / ".claude.json.lock").exists()


def test_stale_native_lease_is_recovered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    lease = tmp_path / ".claude.json.lock"
    lease.mkdir()
    os.utime(lease, (1, 1))
    ClaudeTrust().update(tmp_path / "worktree", accepted=True)
    assert not lease.exists()
