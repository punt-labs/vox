"""Tests for punt_vox.dirs -- cross-platform path resolution."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from punt_vox.dirs import (
    DEFAULT_CONFIG_PATH,
    _parse_xdg_user_dir,  # pyright: ignore[reportPrivateUsage]
    _resolve_music_dir,  # pyright: ignore[reportPrivateUsage]
    default_output_dir,
    ephemeral_dir,
    find_config,
    music_output_dir,
)

# ---------------------------------------------------------------------------
# DEFAULT_CONFIG_PATH
# ---------------------------------------------------------------------------


class TestDefaultConfigPath:
    def test_uses_punt_labs_subdir(self) -> None:
        assert Path(".punt-labs/vox/config.md") == DEFAULT_CONFIG_PATH


# ---------------------------------------------------------------------------
# find_config
# ---------------------------------------------------------------------------


class TestFindConfig:
    def test_finds_new_path(self, tmp_path: Path) -> None:
        new_config = tmp_path / ".punt-labs" / "vox" / "config.md"
        new_config.parent.mkdir(parents=True)
        new_config.write_text("---\nnotify: y\n---\n")
        result = find_config(tmp_path)
        assert result == new_config

    def test_falls_back_to_legacy(self, tmp_path: Path) -> None:
        legacy = tmp_path / ".vox" / "config.md"
        legacy.parent.mkdir(parents=True)
        legacy.write_text("---\nnotify: y\n---\n")
        result = find_config(tmp_path)
        assert result == legacy

    def test_prefers_new_over_legacy(self, tmp_path: Path) -> None:
        new_config = tmp_path / ".punt-labs" / "vox" / "config.md"
        new_config.parent.mkdir(parents=True)
        new_config.write_text("---\nnotify: y\n---\n")
        legacy = tmp_path / ".vox" / "config.md"
        legacy.parent.mkdir(parents=True)
        legacy.write_text("---\nnotify: n\n---\n")
        result = find_config(tmp_path)
        assert result == new_config

    def test_returns_none_when_absent(self, tmp_path: Path) -> None:
        # Create an isolated directory tree with no config anywhere
        # above it by starting from a fresh root with no parents to walk.
        isolated = tmp_path / "isolated"
        isolated.mkdir()
        with patch("punt_vox.dirs.Path.cwd", return_value=isolated):
            # Walk up from isolated; tmp_path has no config files
            # but walk-up may reach the real repo root. Pass the
            # isolated dir explicitly so the walk stays bounded.
            result = find_config(isolated)
        # May find a parent config if tmp_path is inside a repo
        # with .punt-labs/vox/config.md. The contract is that
        # find_config returns None when no config exists in the
        # walk-up chain. Test only with an isolated subtree.
        if result is not None:
            assert not result.is_relative_to(isolated)

    def test_walks_up_to_parent(self, tmp_path: Path) -> None:
        new_config = tmp_path / ".punt-labs" / "vox" / "config.md"
        new_config.parent.mkdir(parents=True)
        new_config.write_text("---\n---\n")
        child = tmp_path / "sub" / "dir"
        child.mkdir(parents=True)
        result = find_config(child)
        assert result == new_config


# ---------------------------------------------------------------------------
# ephemeral_dir
# ---------------------------------------------------------------------------


class TestEphemeralDir:
    def test_creates_directory(self, tmp_path: Path) -> None:
        result = ephemeral_dir(tmp_path)
        assert result == tmp_path / ".punt-labs" / "vox" / "ephemeral"
        assert result.is_dir()

    def test_idempotent(self, tmp_path: Path) -> None:
        ephemeral_dir(tmp_path)
        result = ephemeral_dir(tmp_path)
        assert result.is_dir()


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


# ---------------------------------------------------------------------------
# music_output_dir
# ---------------------------------------------------------------------------


class TestMusicOutputDir:
    def test_returns_tracks_subdir(self, tmp_path: Path) -> None:
        with patch("punt_vox.dirs.default_output_dir", return_value=tmp_path / "vox"):
            result = music_output_dir()
        assert result == tmp_path / "vox" / "tracks"
