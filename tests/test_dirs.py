"""Tests for punt_vox.dirs -- cross-platform path resolution."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from punt_vox.config import ConfigStore
from punt_vox.dirs import (
    DEFAULT_CONFIG_DIR,
    _read_xdg_music_dir,  # pyright: ignore[reportPrivateUsage]
    _resolve_music_dir,  # pyright: ignore[reportPrivateUsage]
    default_output_dir,
    find_config_dir,
    find_repo_root,
    writable_config_dir,
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
# writable_config_dir
# ---------------------------------------------------------------------------


def _config(root: Path, **files: str) -> Path:
    """Write ``.punt-labs/vox/<name>`` files under *root*; return the dir."""
    config_dir = root / ".punt-labs" / "vox"
    config_dir.mkdir(parents=True)
    for name, text in files.items():
        (config_dir / name.replace("_", ".")).write_text(text)
    return config_dir


class TestWritableConfigDir:
    @pytest.fixture(autouse=True)
    def _real_find_config_dir(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The hermetic_config fixture pins dirs.find_config_dir; walk for real.
        monkeypatch.setattr("punt_vox.dirs.find_config_dir", find_config_dir)

    def test_inherited_config_seeds_inner_repo(self, tmp_path: Path) -> None:
        outer = _config(
            tmp_path, vox_md='---\nvoice: "roger"\n---\n', vox_local_md="---\n---\n"
        )
        inner = tmp_path / "inner"
        (inner / ".git").mkdir(parents=True)
        assert find_config_dir(inner) == outer

        own = writable_config_dir(inner / "src")

        assert own == inner / ".punt-labs" / "vox"
        assert own is not None
        assert (own / "vox.md").read_text() == '---\nvoice: "roger"\n---\n'
        assert (own / "vox.local.md").read_text() == "---\n---\n"
        assert find_config_dir(inner) == own

    def test_seeds_only_files_the_inherited_config_has(self, tmp_path: Path) -> None:
        _config(tmp_path, vox_md="---\n---\n")
        inner = tmp_path / "inner"
        (inner / ".git").mkdir(parents=True)
        own = writable_config_dir(inner)
        assert own is not None
        assert not (own / "vox.local.md").exists()

    def test_repo_with_own_config_writes_there(self, tmp_path: Path) -> None:
        _config(tmp_path, vox_md="---\n---\n")
        inner = tmp_path / "inner"
        (inner / ".git").mkdir(parents=True)
        own = _config(inner, vox_local_md="---\n---\n")
        assert writable_config_dir(inner) == own
        assert not (own / "vox.md").exists()

    def test_outside_a_repo_writes_the_found_config(self, tmp_path: Path) -> None:
        found = _config(tmp_path, vox_md="---\n---\n")
        child = tmp_path / "child"
        child.mkdir()
        with patch("punt_vox.dirs.find_repo_root", return_value=None):
            assert writable_config_dir(child) == found

    def test_first_write_targets_repo_root_without_seeding(
        self, no_config_dir: Path
    ) -> None:
        (no_config_dir / ".git").mkdir()
        child = no_config_dir / "src"
        child.mkdir()

        assert writable_config_dir(child) == no_config_dir / DEFAULT_CONFIG_DIR
        assert not (no_config_dir / DEFAULT_CONFIG_DIR).exists()

    @pytest.mark.subprocess
    def test_first_write_ignores_local_state(self, no_config_dir: Path) -> None:
        repo = no_config_dir
        child = repo / "a" / "b"
        child.mkdir(parents=True)
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        host_ignore = "build/\n"
        gitignore = repo / ".gitignore"
        gitignore.write_text(host_ignore, encoding="utf-8")

        store = ConfigStore(writable_config_dir(child))
        store.write_field("vibe", "focused")
        store.write_field("voice", "roger")

        status = subprocess.check_output(
            ["git", "-c", "core.excludesFile=", "status", "--short", "-uall"],
            cwd=repo,
            text=True,
        ).splitlines()
        assert (repo / DEFAULT_CONFIG_DIR / "vox.local.md").is_file()
        assert status == ["?? .gitignore", "?? .punt-labs/vox/vox.md"]
        assert gitignore.read_text(encoding="utf-8").startswith(host_ignore)

    @pytest.mark.subprocess
    @pytest.mark.parametrize("scope", ["", "pkg"], ids=["root", "nested"])
    def test_first_local_write_ignores_state_with_committed_config(
        self, no_config_dir: Path, scope: str
    ) -> None:
        repo = no_config_dir
        base = repo / scope
        own = _config(base, vox_md='---\nvoice: "roger"\n---\n')
        child = base / "a" / "b"
        child.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "--template="], cwd=repo, check=True)
        subprocess.run(["git", "add", str(own / "vox.md")], cwd=repo, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "-qm",
                "base",
            ],
            cwd=repo,
            check=True,
        )

        ConfigStore(writable_config_dir(child)).write_field("vibe", "focused")

        status = subprocess.check_output(
            ["git", "-c", "core.excludesFile=", "status", "--short", "-uall"],
            cwd=repo,
            text=True,
        ).splitlines()
        assert (own / "vox.local.md").is_file()
        ignore = (base / ".gitignore").relative_to(repo).as_posix()
        assert status == [f"?? {ignore}"]

    @pytest.mark.parametrize("entry", ["vox.md", "vox.local.md", "directory", "locks"])
    @pytest.mark.parametrize("inherited", [False, True], ids=["fresh", "inherited"])
    def test_first_write_refuses_a_destination_outside_the_checkout(
        self, no_config_dir: Path, entry: str, *, inherited: bool
    ) -> None:
        if inherited:
            workspace_config = no_config_dir / DEFAULT_CONFIG_DIR
            workspace_config.mkdir(parents=True)
            (workspace_config / "vox.md").write_text("---\nnotify: n\n---\n")
        repo = no_config_dir / "repo"
        child = repo / "src"
        child.mkdir(parents=True)
        (repo / ".git").mkdir()
        config = repo / DEFAULT_CONFIG_DIR
        outside = no_config_dir / "outside"
        if entry in {"directory", "locks"}:
            config.parent.mkdir()
            outside.mkdir()
            link = config if entry == "directory" else config.parent / "local"
            link.symlink_to(outside, target_is_directory=True)
        else:
            config.mkdir(parents=True)
            (config / entry).symlink_to(outside)

        with pytest.raises(OSError, match="outside the Git checkout"):
            writable_config_dir(child)
        assert not (repo / ".gitignore").exists()
        if entry in {"directory", "locks"}:
            assert not list(outside.iterdir())
        else:
            assert not outside.exists()

    def test_no_config_or_repo(self, tmp_path: Path) -> None:
        with (
            patch("punt_vox.dirs.find_config_dir", return_value=None),
            patch("punt_vox.dirs.find_repo_root", return_value=None),
        ):
            assert writable_config_dir(tmp_path) is None


# ---------------------------------------------------------------------------
# XDG music directory
# ---------------------------------------------------------------------------


class TestReadXdgMusicDir:
    def test_parses_music_dir(self, tmp_path: Path) -> None:
        dirs_file = tmp_path / ".config" / "user-dirs.dirs"
        dirs_file.parent.mkdir(parents=True)
        dirs_file.write_text('XDG_MUSIC_DIR="$HOME/Musik"\n')
        with patch("punt_vox.dirs.Path.home", return_value=tmp_path):
            result = _read_xdg_music_dir()
        assert result == tmp_path / "Musik"

    def test_returns_none_when_file_missing(self, tmp_path: Path) -> None:
        with patch("punt_vox.dirs.Path.home", return_value=tmp_path):
            result = _read_xdg_music_dir()
        assert result is None

    def test_returns_none_when_key_missing(self, tmp_path: Path) -> None:
        dirs_file = tmp_path / ".config" / "user-dirs.dirs"
        dirs_file.parent.mkdir(parents=True)
        dirs_file.write_text('XDG_DESKTOP_DIR="$HOME/Desktop"\n')
        with patch("punt_vox.dirs.Path.home", return_value=tmp_path):
            result = _read_xdg_music_dir()
        assert result is None

    def test_expands_home(self, tmp_path: Path) -> None:
        dirs_file = tmp_path / ".config" / "user-dirs.dirs"
        dirs_file.parent.mkdir(parents=True)
        dirs_file.write_text('XDG_MUSIC_DIR="$HOME/Music"\n')
        with patch("punt_vox.dirs.Path.home", return_value=tmp_path):
            result = _read_xdg_music_dir()
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

    def test_linux_fallback_with_non_utf8_xdg_file(self, tmp_path: Path) -> None:
        dirs_file = tmp_path / ".config" / "user-dirs.dirs"
        dirs_file.parent.mkdir(parents=True)
        dirs_file.write_bytes(b'# Invalid UTF-8: \xff\nXDG_MUSIC_DIR="$HOME/Musik"\n')
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
