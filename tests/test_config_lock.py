"""Tests for punt_vox.config_lock -- where a config file's lock lives."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import pytest

from punt_vox.config_lock import ConfigLock
from punt_vox.sibling_lock import SiblingLock

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("filename", ["vox.md", "vox.local.md"])
def test_repo_config_locks_in_the_local_zone(tmp_path: Path, filename: str) -> None:
    config = tmp_path / ".punt-labs" / "vox" / filename
    assert ConfigLock(config).path == (
        tmp_path / ".punt-labs" / "local" / "vox" / f"{filename}.lock"
    )


def test_other_configs_lock_under_the_run_dir_by_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("VOX_STATE_DIR", str(tmp_path / "other state"))
    config = tmp_path / "one" / "vox.md"
    digest = hashlib.sha256(str(config.resolve()).encode()).hexdigest()
    assert ConfigLock(config).path == (
        tmp_path / "home/.punt-labs/vox/run/config-locks" / digest
    )


def test_aliases_of_one_config_share_a_lock(tmp_path: Path) -> None:
    config = tmp_path / ".punt-labs" / "vox" / "vox.md"
    alias = tmp_path / ".punt-labs" / "vox" / ".." / "vox" / "vox.md"
    assert ConfigLock(alias).path == ConfigLock(config).path


def test_sibling_is_a_world_writable_lock_at_that_path(tmp_path: Path) -> None:
    config = tmp_path / ".punt-labs" / "vox" / "vox.md"
    lock = ConfigLock(config).sibling()
    assert isinstance(lock, SiblingLock)
    assert lock.path == ConfigLock(config).path
