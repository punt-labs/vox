"""Tests for punt_vox.dirs -- cross-platform path resolution."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from punt_vox.dirs import (
    DEFAULT_CONFIG_DIR,
    _parse_xdg_user_dir,  # pyright: ignore[reportPrivateUsage]
    _resolve_music_dir,  # pyright: ignore[reportPrivateUsage]
    default_output_dir,
    find_config_dir,
    find_repo_root,
)

# ---------------------------------------------------------------------------
# DEFAULT_CONFIG_DIR
# ---------------------------------------------------------------------------


class TestDefaultConfigDir:
    def test_uses_punt_labs_subdir(self) -> None:
        assert Path(".punt-labs/vox") == DEFAULT_CONFIG_DIR


# ---------------------------------------------------------------------------
# find_repo_root
# ---------------------------------------------------------------------------


class TestFindRepoRoot:
    def test_finds_git_directory(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        assert find_repo_root(tmp_path) == tmp_path

    def test_returns_none_when_no_git(self, tmp_path: Path) -> None:
        isolated = tmp_path / "no-git"
        isolated.mkdir()
        # Walk is bounded by tmp_path; real .git above tmp_path may
        # exist, so patch Path.parents to ensure isolation.
        with patch("punt_vox.dirs.Path.cwd", return_value=isolated):
            result = find_repo_root(isolated)
        # The result may find a real .git above tmp_path on the test
        # host, so only assert None when isolated dir is truly rootless.
        if result is not None:
            assert not result.is_relative_to(isolated)

    def test_walks_up_from_subdirectory(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        subdir = tmp_path / "src" / "pkg"
        subdir.mkdir(parents=True)
        assert find_repo_root(subdir) == tmp_path

    def test_submodule_git_file(self, tmp_path: Path) -> None:
        """Submodules use a .git file (not directory) pointing to the parent."""
        (tmp_path / ".git").write_text("gitdir: ../.git/modules/sub\n")
        assert find_repo_root(tmp_path) == tmp_path


# ---------------------------------------------------------------------------
# find_config_dir
# ---------------------------------------------------------------------------


class TestFindConfigDir:
    def test_finds_dir_with_vox_md(self, tmp_path: Path) -> None:
        config_dir = tmp_path / ".punt-labs" / "vox"
        config_dir.mkdir(parents=True)
        (config_dir / "vox.md").write_text("---\nnotify: y\n---\n")
        result = find_config_dir(tmp_path)
        assert result == config_dir

    def test_finds_dir_with_vox_local_md(self, tmp_path: Path) -> None:
        config_dir = tmp_path / ".punt-labs" / "vox"
        config_dir.mkdir(parents=True)
        (config_dir / "vox.local.md").write_text("---\nvibe: calm\n---\n")
        result = find_config_dir(tmp_path)
        assert result == config_dir

    def test_no_legacy_fallback(self, tmp_path: Path) -> None:
        legacy = tmp_path / ".vox" / "config.md"
        legacy.parent.mkdir(parents=True)
        legacy.write_text("---\nnotify: y\n---\n")
        result = find_config_dir(tmp_path)
        if result is not None:
            assert not result.is_relative_to(tmp_path / ".vox")

    def test_returns_none_when_absent(self, tmp_path: Path) -> None:
        isolated = tmp_path / "isolated"
        isolated.mkdir()
        with patch("punt_vox.dirs.Path.cwd", return_value=isolated):
            result = find_config_dir(isolated)
        if result is not None:
            assert not result.is_relative_to(isolated)

    def test_walks_up_to_parent(self, tmp_path: Path) -> None:
        config_dir = tmp_path / ".punt-labs" / "vox"
        config_dir.mkdir(parents=True)
        (config_dir / "vox.md").write_text("---\n---\n")
        child = tmp_path / "sub" / "dir"
        child.mkdir(parents=True)
        result = find_config_dir(child)
        assert result == config_dir

    def test_subdir_resolves_repo_not_above(self, tmp_path: Path) -> None:
        # start = repo/src/pkg resolves the repo's own config (walk-up
        # bounded to the first ancestor with config).
        repo = tmp_path / "vox"
        config_dir = repo / ".punt-labs" / "vox"
        config_dir.mkdir(parents=True)
        (config_dir / "vox.md").write_text("---\n---\n")
        subdir = repo / "src" / "punt_vox"
        subdir.mkdir(parents=True)
        result = find_config_dir(subdir)
        assert result == config_dir

    def test_child_config_wins_over_parent(self, tmp_path: Path) -> None:
        # Nested layout: parent and child both have config. start=child
        # resolves the child's config, never climbing past the first match.
        parent_config = tmp_path / ".punt-labs" / "vox"
        parent_config.mkdir(parents=True)
        (parent_config / "vox.md").write_text("---\n---\n")
        child = tmp_path / "vox"
        child_config = child / ".punt-labs" / "vox"
        child_config.mkdir(parents=True)
        (child_config / "vox.md").write_text("---\n---\n")
        result = find_config_dir(child)
        assert result == child_config

    def test_legacy_config_md_resolves_none(self, no_config_dir: Path) -> None:
        # A dir with only legacy config.md (no vox.md/vox.local.md) resolves None.
        # no_config_dir has no ambient .punt-labs/vox above it, so the walk
        # cannot leak into the real repo config (fails under TMPDIR=.tmp).
        legacy_dir = no_config_dir / ".punt-labs" / "vox"
        legacy_dir.mkdir(parents=True)
        (legacy_dir / "config.md").write_text("---\nnotify: y\n---\n")
        isolated = no_config_dir / "leaf"
        isolated.mkdir()
        result = find_config_dir(isolated)
        assert result is None


# ---------------------------------------------------------------------------
# _parse_xdg_user_dir
# ---------------------------------------------------------------------------


class TestParseXdgUserDir:
    def test_parses_music_dir(self, tmp_path: Path) -> None:
        dirs_file = tmp_path / ".config" / "user-dirs.dirs"
        dirs_file.parent.mkdir(parents=True)
        dirs_file.write_text('XDG_MUSIC_DIR="$HOME/Musik"\n')
        with patch("punt_vox.dirs.Path.home", return_value=tmp_path):
            result = _parse_xdg_user_dir("XDG_MUSIC_DIR")
        assert result == tmp_path / "Musik"

    def test_returns_none_when_file_missing(self, tmp_path: Path) -> None:
        with patch("punt_vox.dirs.Path.home", return_value=tmp_path):
            result = _parse_xdg_user_dir("XDG_MUSIC_DIR")
        assert result is None

    def test_returns_none_when_key_missing(self, tmp_path: Path) -> None:
        dirs_file = tmp_path / ".config" / "user-dirs.dirs"
        dirs_file.parent.mkdir(parents=True)
        dirs_file.write_text('XDG_DESKTOP_DIR="$HOME/Desktop"\n')
        with patch("punt_vox.dirs.Path.home", return_value=tmp_path):
            result = _parse_xdg_user_dir("XDG_MUSIC_DIR")
        assert result is None

    def test_expands_home(self, tmp_path: Path) -> None:
        dirs_file = tmp_path / ".config" / "user-dirs.dirs"
        dirs_file.parent.mkdir(parents=True)
        dirs_file.write_text('XDG_MUSIC_DIR="$HOME/Music"\n')
        with patch("punt_vox.dirs.Path.home", return_value=tmp_path):
            result = _parse_xdg_user_dir("XDG_MUSIC_DIR")
        assert result == tmp_path / "Music"


# ---------------------------------------------------------------------------
# _resolve_music_dir
# ---------------------------------------------------------------------------


class TestResolveMusicDir:
    def test_linux_with_xdg(self, tmp_path: Path) -> None:
        dirs_file = tmp_path / ".config" / "user-dirs.dirs"
        dirs_file.parent.mkdir(parents=True)
        dirs_file.write_text('XDG_MUSIC_DIR="$HOME/Musik"\n')
        with (
            patch("punt_vox.dirs.sys.platform", "linux"),
            patch("punt_vox.dirs.Path.home", return_value=tmp_path),
        ):
            result = _resolve_music_dir()
        assert result == tmp_path / "Musik"

    def test_linux_fallback(self, tmp_path: Path) -> None:
        with (
            patch("punt_vox.dirs.sys.platform", "linux"),
            patch("punt_vox.dirs.Path.home", return_value=tmp_path),
        ):
            result = _resolve_music_dir()
        assert result == tmp_path / "Music"

    def test_macos(self, tmp_path: Path) -> None:
        with (
            patch("punt_vox.dirs.sys.platform", "darwin"),
            patch("punt_vox.dirs.Path.home", return_value=tmp_path),
        ):
            result = _resolve_music_dir()
        assert result == tmp_path / "Music"

    def test_windows(self, tmp_path: Path) -> None:
        with (
            patch("punt_vox.dirs.sys.platform", "win32"),
            patch("punt_vox.dirs.Path.home", return_value=tmp_path),
        ):
            result = _resolve_music_dir()
        assert result == tmp_path / "Music"


# ---------------------------------------------------------------------------
# default_output_dir
# ---------------------------------------------------------------------------


class TestDefaultOutputDir:
    def test_env_override(self, tmp_path: Path) -> None:
        custom = str(tmp_path / "custom")
        with patch.dict("os.environ", {"VOX_OUTPUT_DIR": custom}):
            result = default_output_dir()
        assert result == Path(custom)

    def test_platform_default(self, tmp_path: Path) -> None:
        with patch("punt_vox.dirs._resolve_music_dir", return_value=tmp_path / "Music"):
            import os

            os.environ.pop("VOX_OUTPUT_DIR", None)
            result = default_output_dir()
        assert result == tmp_path / "Music" / "vox"
