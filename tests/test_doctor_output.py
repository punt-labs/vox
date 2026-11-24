"""Tests for punt_vox.doctor_output.OutputDirCheck."""

from __future__ import annotations

import errno
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from punt_vox.__main__ import app
from punt_vox.doctor_output import OutputDirCheck
from punt_vox.doctor_result import CheckResult

_MUSIC_ON_ADVICE = "created when 'vox music on' saves its first album"


class TestMusicDir:
    @staticmethod
    def _check(music_dir: Path, output_dir: Path) -> list[CheckResult]:
        with (
            patch("punt_vox.doctor_output._resolve_music_dir", return_value=music_dir),
            patch("punt_vox.doctor_output.default_output_dir", return_value=output_dir),
        ):
            return OutputDirCheck().music_dir()

    def test_present_dir_reports_nothing(self, tmp_path: Path) -> None:
        assert self._check(tmp_path, tmp_path / "vox") == []

    def test_missing_dir_names_the_command_that_creates_it(
        self, tmp_path: Path
    ) -> None:
        missing = tmp_path / "Music"
        results = self._check(missing, missing / "vox")
        assert len(results) == 1
        assert results[0].symbol == "⚠"
        assert results[0].message == f"output music dir: absent — {_MUSIC_ON_ADVICE}"
        assert str(missing) not in results[0].message  # no absolute path

    def test_output_override_outside_music_dir_reports_nothing(
        self, tmp_path: Path
    ) -> None:
        assert self._check(tmp_path / "Music", tmp_path / "albums") == []

    def test_advised_command_exists(self) -> None:
        result = CliRunner().invoke(app, ["music", "on", "--help"])
        assert result.exit_code == 0, result.output


class TestOutputDir:
    def test_writable_dir_reports_verdict_no_path(self, tmp_path: Path) -> None:
        with patch("punt_vox.doctor_output.default_output_dir", return_value=tmp_path):
            results = OutputDirCheck().output_dir()
        assert len(results) == 1
        assert results[0].passed is True
        assert results[0].message == "output: writable"
        assert str(tmp_path) not in results[0].message  # no absolute path

    def test_probe_leaves_existing_files_untouched(self, tmp_path: Path) -> None:
        existing = tmp_path / ".doctor_test"
        existing.write_text("user data")
        with patch("punt_vox.doctor_output.default_output_dir", return_value=tmp_path):
            results = OutputDirCheck().output_dir()
        assert results[0].passed is True
        assert existing.read_text() == "user data"
        assert sorted(p.name for p in tmp_path.iterdir()) == [".doctor_test"]

    def test_probe_does_not_write_through_symlink(self, tmp_path: Path) -> None:
        out_dir = tmp_path / "output"
        out_dir.mkdir()
        target = tmp_path / "elsewhere.txt"
        target.write_text("keep me")
        (out_dir / ".doctor_test").symlink_to(target)
        with patch("punt_vox.doctor_output.default_output_dir", return_value=out_dir):
            results = OutputDirCheck().output_dir()
        assert results[0].passed is True
        assert target.read_text() == "keep me"
        assert (out_dir / ".doctor_test").is_symlink()

    def test_unwritable_dir_reports_failure(self, tmp_path: Path) -> None:
        with (
            patch("punt_vox.doctor_output.default_output_dir", return_value=tmp_path),
            patch("punt_vox.doctor_output.tempfile.TemporaryFile", side_effect=OSError),
        ):
            results = OutputDirCheck().output_dir()
        assert results[0].passed is False
        assert results[0].message == "output: not writable — check permissions"

    def test_full_disk_reports_failure(self, tmp_path: Path) -> None:
        with (
            patch("punt_vox.doctor_output.default_output_dir", return_value=tmp_path),
            patch("punt_vox.doctor_output.tempfile.TemporaryFile") as temporary_file,
        ):
            probe = temporary_file.return_value.__enter__.return_value
            probe.write.side_effect = OSError(errno.ENOSPC, "No space left on device")
            results = OutputDirCheck().output_dir()
        temporary_file.assert_called_once_with(buffering=0, dir=tmp_path)
        assert results[0].passed is False
        assert results[0].message == "output: not writable — check permissions"

    def test_missing_dir(self, tmp_path: Path) -> None:
        missing = tmp_path / "nonexistent"
        with patch("punt_vox.doctor_output.default_output_dir", return_value=missing):
            results = OutputDirCheck().output_dir()
        assert len(results) == 1
        assert results[0].passed is False
        assert results[0].symbol == "⚠"
        assert results[0].message == f"output: absent — {_MUSIC_ON_ADVICE}"
        assert str(missing) not in results[0].message  # no absolute path
