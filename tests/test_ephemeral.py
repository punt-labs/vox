"""Tests for punt_vox.ephemeral."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from punt_vox.ephemeral import (
    clean_ephemeral,
    ephemeral_output_dir,
    project_root,
)


def _mock_config_path(root: Path) -> Path:
    """Return a config path that makes project_root() resolve to *root*."""
    return root / ".vox" / "config.md"


class TestProjectRoot:
    def test_derives_root_from_config_path(self, tmp_path: Path) -> None:
        config = _mock_config_path(tmp_path)
        with patch("punt_vox.ephemeral.resolve_config_path", return_value=config):
            assert project_root() == tmp_path

    def test_falls_back_to_cwd_when_root_is_slash(self, tmp_path: Path) -> None:
        # config at /.vox/config.md → root would be /, should fall back
        config = Path("/.vox/config.md")
        with (
            patch("punt_vox.ephemeral.resolve_config_path", return_value=config),
            patch("punt_vox.ephemeral.Path.cwd", return_value=tmp_path),
        ):
            assert project_root() == tmp_path


class TestEphemeralOutputDir:
    def test_creates_directory(self, tmp_path: Path) -> None:
        config = _mock_config_path(tmp_path)
        with patch("punt_vox.ephemeral.resolve_config_path", return_value=config):
            result = ephemeral_output_dir()
            assert result == tmp_path / ".vox"
            assert result.is_dir()

    def test_idempotent(self, tmp_path: Path) -> None:
        config = _mock_config_path(tmp_path)
        with patch("punt_vox.ephemeral.resolve_config_path", return_value=config):
            first = ephemeral_output_dir()
            second = ephemeral_output_dir()
            assert first == second
            assert first.is_dir()


class TestCleanEphemeral:
    def test_deletes_all_files(self, tmp_path: Path) -> None:
        config = _mock_config_path(tmp_path)
        eph = tmp_path / ".vox"
        eph.mkdir()
        (eph / "a.mp3").write_text("audio")
        (eph / "b.mp3").write_text("audio")

        with patch("punt_vox.ephemeral.resolve_config_path", return_value=config):
            deleted = clean_ephemeral()

        assert deleted == 2
        assert list(eph.iterdir()) == []

    def test_keeps_specified_file(self, tmp_path: Path) -> None:
        config = _mock_config_path(tmp_path)
        eph = tmp_path / ".vox"
        eph.mkdir()
        keep = eph / "playing.mp3"
        keep.write_text("audio")
        (eph / "old.mp3").write_text("audio")

        with patch("punt_vox.ephemeral.resolve_config_path", return_value=config):
            deleted = clean_ephemeral(keep=keep)

        assert deleted == 1
        assert keep.exists()
        assert not (eph / "old.mp3").exists()

    def test_preserves_non_mp3_files(self, tmp_path: Path) -> None:
        config = _mock_config_path(tmp_path)
        eph = tmp_path / ".vox"
        eph.mkdir()
        cfg = eph / "config.md"
        cfg.write_text("---\nnotify: y\n---\n")
        (eph / "old.mp3").write_text("audio")

        with patch("punt_vox.ephemeral.resolve_config_path", return_value=config):
            deleted = clean_ephemeral()

        assert deleted == 1
        assert cfg.exists()
        assert not (eph / "old.mp3").exists()

    def test_no_directory_returns_zero(self, tmp_path: Path) -> None:
        config = _mock_config_path(tmp_path)
        with patch("punt_vox.ephemeral.resolve_config_path", return_value=config):
            assert clean_ephemeral() == 0

    def test_empty_directory_returns_zero(self, tmp_path: Path) -> None:
        config = _mock_config_path(tmp_path)
        (tmp_path / ".vox").mkdir()
        with patch("punt_vox.ephemeral.resolve_config_path", return_value=config):
            assert clean_ephemeral() == 0
