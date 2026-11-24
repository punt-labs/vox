"""Tests for punt_vox.config_seed -- seeding a nested repo's own config."""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from punt_vox.config_seed import InheritedConfig

_VOICE = '---\nvoice: "roger"\n---\n'


@pytest.fixture
def inherited(tmp_path: Path) -> Path:
    """The workspace config directory a nested repo inherits."""
    source = tmp_path / "workspace" / ".punt-labs" / "vox"
    source.mkdir(parents=True)
    (source / "vox.md").write_text(_VOICE)
    (source / "vox.local.md").write_text('---\nvibe: "calm"\n---\n')
    return source


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A nested git repo with no vox config of its own."""
    root = tmp_path / "workspace" / "inner"
    (root / ".git").mkdir(parents=True)
    return root


def _own(repo: Path) -> Path:
    return repo / ".punt-labs" / "vox"


class TestSeed:
    def test_copies_every_inherited_file(self, inherited: Path, repo: Path) -> None:
        InheritedConfig(inherited).seed(repo, _own(repo))
        for name in ("vox.md", "vox.local.md"):
            assert (_own(repo) / name).read_text() == (inherited / name).read_text()
        assert not list(_own(repo).parent.glob(".vox-seed-*"))

    def test_gitignores_the_copied_local_state(
        self, inherited: Path, repo: Path
    ) -> None:
        (repo / ".gitignore").write_text("build/\n")
        InheritedConfig(inherited).seed(repo, _own(repo))
        rules = (repo / ".gitignore").read_text().splitlines()
        assert rules[0] == "build/"
        assert ".punt-labs/vox/vox.local.md" in rules

    def test_symlinked_gitignore_is_left_alone(
        self, inherited: Path, repo: Path, tmp_path: Path
    ) -> None:
        target = tmp_path / "elsewhere"
        target.write_text("x\n")
        (repo / ".gitignore").symlink_to(target)
        InheritedConfig(inherited).seed(repo, _own(repo))
        assert target.read_text() == "x\n"
        assert (_own(repo) / "vox.md").read_text() == _VOICE

    def test_links_into_an_existing_dir_without_config(
        self, inherited: Path, repo: Path
    ) -> None:
        marker = _own(repo) / "enabled"
        marker.parent.mkdir(parents=True)
        marker.touch()
        InheritedConfig(inherited).seed(repo, _own(repo))
        assert (_own(repo) / "vox.md").read_text() == _VOICE
        assert marker.exists()
        assert not list(_own(repo).parent.glob(".vox-seed-*"))

    def test_never_replaces_a_concurrent_writers_file(
        self, inherited: Path, repo: Path
    ) -> None:
        _own(repo).mkdir(parents=True)
        (_own(repo) / "vox.md").write_text("---\nnotify: c\n---\n")
        InheritedConfig(inherited).seed(repo, _own(repo))
        assert (_own(repo) / "vox.md").read_text() == "---\nnotify: c\n---\n"
        assert (_own(repo) / "vox.local.md").exists()

    def test_keeps_the_inherited_file_mode(self, inherited: Path, repo: Path) -> None:
        (inherited / "vox.md").chmod(0o600)
        InheritedConfig(inherited).seed(repo, _own(repo))
        assert (_own(repo) / "vox.md").stat().st_mode & 0o777 == 0o600

    def test_failed_copy_leaves_nothing_behind(
        self, inherited: Path, repo: Path
    ) -> None:
        real_copy = shutil.copy2
        calls: list[Path] = []

        def copy_then_fail(src: Path, dst: Path) -> object:
            calls.append(src)
            if len(calls) == 2:
                raise OSError(28, "No space left on device")
            return real_copy(src, dst)

        with (
            patch("punt_vox.config_seed.shutil.copy2", copy_then_fail),
            pytest.raises(OSError, match="No space"),
        ):
            InheritedConfig(inherited).seed(repo, _own(repo))
        assert not _own(repo).exists()
        assert not list(_own(repo).parent.glob(".vox-seed-*"))
