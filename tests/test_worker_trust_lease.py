"""Trust edits participate in Claude's native directory lease."""

from __future__ import annotations

import json
import os
from pathlib import Path
from threading import Event
from unittest.mock import Mock, patch

import pytest

from punt_vox.atomic_file import AtomicFile
from punt_vox.voxd.workers.claude_lease import ClaudeConfigLease
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


def test_native_lease_refuses_a_non_directory(tmp_path: Path) -> None:
    path = tmp_path / ".claude.json.lock"
    path.write_text("unrelated file")
    with (
        pytest.raises(ValueError, match="must be a directory"),
        ClaudeConfigLease(tmp_path / ".claude.json").held(),
    ):
        pytest.fail("a file is not a native directory lease")
    assert path.read_text() == "unrelated file"


@pytest.mark.parametrize("outcome", ["refresh", "replaced", "utime_error"])
def test_native_heartbeat_respects_ownership(tmp_path: Path, outcome: str) -> None:
    path = tmp_path / ".claude.json.lock"
    path.mkdir()
    identity = path.stat()
    if outcome == "replaced":
        path.rename(tmp_path / "previous-owner")
        path.mkdir()
    stop = Mock(spec=Event)
    stop.wait.side_effect = [False, True]
    with patch(
        "punt_vox.voxd.workers.claude_lease.os.utime",
        side_effect=OSError("touch failed") if outcome == "utime_error" else None,
    ) as touch:
        ClaudeConfigLease(tmp_path / ".claude.json")._refresh(stop, identity)
    if outcome == "replaced":
        touch.assert_not_called()
    else:
        touch.assert_called_once_with(path, follow_symlinks=False)


def test_missing_native_lease_has_no_owner(tmp_path: Path) -> None:
    assert not ClaudeConfigLease(tmp_path / ".claude.json")._owns(tmp_path.stat())


def test_heartbeat_start_failure_releases_its_lease(tmp_path: Path) -> None:
    with (
        patch(
            "punt_vox.voxd.workers.claude_lease.Thread.start",
            side_effect=RuntimeError("thread failed"),
        ),
        pytest.raises(RuntimeError, match="thread failed"),
        ClaudeConfigLease(tmp_path / ".claude.json").held(),
    ):
        pytest.fail("a failed heartbeat cannot establish a refreshed lease")
    assert not (tmp_path / ".claude.json.lock").exists()


def test_lease_release_never_removes_a_new_owners_directory(tmp_path: Path) -> None:
    path = tmp_path / ".claude.json.lock"
    with ClaudeConfigLease(tmp_path / ".claude.json").held():
        path.rename(tmp_path / "previous-owner")
        path.mkdir()
    assert path.is_dir()
