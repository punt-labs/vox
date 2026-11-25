"""Tests for punt_vox.repo_config -- config resolved per access from a directory."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from punt_vox import dirs
from punt_vox.config import ConfigStore
from punt_vox.repo_config import RepoConfig

if TYPE_CHECKING:
    from pathlib import Path

_INHERITED = '---\nnotify: "n"\nvoice: "roger"\n---\n'


@pytest.fixture(autouse=True)
def _real_resolvers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Undo the hermetic fixture's pinned resolvers; these tests build real repos."""
    monkeypatch.setattr(dirs, "find_config_dir", _REAL_FIND)
    monkeypatch.setattr(dirs, "writable_config_dir", _REAL_WRITABLE)


_REAL_FIND = dirs.find_config_dir
_REAL_WRITABLE = dirs.writable_config_dir


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """A workspace git repo with a vox config of its own."""
    root = tmp_path / "workspace"
    (root / ".git").mkdir(parents=True)
    config = root / ".punt-labs" / "vox"
    config.mkdir(parents=True)
    (config / "vox.md").write_text(_INHERITED)
    return root


@pytest.fixture
def inner(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The working directory: a git repo nested in *workspace*, no config of its own."""
    root = workspace / "inner"
    (root / ".git").mkdir(parents=True)
    monkeypatch.chdir(root)
    return root


class TestRepoConfig:
    def test_read_inherits_without_seeding(self, workspace: Path, inner: Path) -> None:
        assert RepoConfig().read().voice == "roger"
        assert not (inner / ".punt-labs").exists()

    def test_write_lands_in_the_inner_repo(self, workspace: Path, inner: Path) -> None:
        RepoConfig().write_field("voice", "matilda")
        assert (workspace / ".punt-labs" / "vox" / "vox.md").read_text() == _INHERITED
        own = ConfigStore(inner / ".punt-labs" / "vox")
        assert own.read_field("voice") == "matilda"
        assert own.read_field("notify") == "n"

    def test_one_holder_reads_its_own_write(self, workspace: Path, inner: Path) -> None:
        """A long-lived holder (the panel) sees the seeded copy after writing."""
        config = RepoConfig()
        config.write_fields({"voice": "matilda"})
        assert config.read().voice == "matilda"

    def test_durable_only_reaches_the_store(self, workspace: Path, inner: Path) -> None:
        local = workspace / ".punt-labs" / "vox" / "vox.local.md"
        local.write_text('---\nvibe_mode: "off"\n---\n')
        assert RepoConfig(durable_only=True).read().vibe_mode != "off"
        assert RepoConfig().read().vibe_mode == "off"
