"""Tests for punt_vox.doctor_output.OutputDirCheck."""

from __future__ import annotations

import errno
from pathlib import Path
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient
from typer.testing import CliRunner

from punt_vox.__main__ import app
from punt_vox.doctor_output import OutputDirCheck
from punt_vox.doctor_result import CheckResult
from punt_vox.types_health import HealthStatus
from punt_vox.voxd import build_app
from punt_vox.voxd.output_health import OutputHealth

_MUSIC_ON_ADVICE = "created when 'vox music on' saves its first album"


@pytest.mark.parametrize("daemon_override", [False, True])
def test_doctor_uses_the_running_daemons_output_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, daemon_override: bool
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("VOX_OUTPUT_DIR", raising=False)
    if daemon_override:
        daemon_output = tmp_path / "daemon-output"
        daemon_output.mkdir()
        monkeypatch.setenv("VOX_OUTPUT_DIR", str(daemon_output))
    daemon = build_app(auth_token="doctor-test")

    shell_output = tmp_path / "shell-output"
    if not daemon_override:
        shell_output.mkdir()
    monkeypatch.setenv("VOX_OUTPUT_DIR", str(shell_output))

    with (
        TestClient(daemon) as client,
        client.websocket_connect("/ws?token=doctor-test") as ws,
        patch("punt_vox.__main__.VoxClientSync") as shell_client,
    ):
        ws.send_json({"type": "health"})
        assert "output_dir" not in ws.receive_json()
        ws.send_json({"type": "health", "output_dirs": True})
        wire = ws.receive_json()
        shell_client.return_value.health.return_value = HealthStatus.from_wire(wire)
        result = CliRunner().invoke(app, ["doctor"])
        shell_client.return_value.health.assert_called_once_with(output_dirs=True)

    if daemon_override:
        assert "✓ output: writable" in result.output
        assert "output music dir:" not in result.output
    else:
        assert f"⚠ output: absent — {_MUSIC_ON_ADVICE}" in result.output
        assert f"⚠ output music dir: absent — {_MUSIC_ON_ADVICE}" in result.output
        assert "output: writable" not in result.output
    assert str(tmp_path) not in str(wire)


class TestMusicDir:
    @staticmethod
    def _check(music_dir: Path) -> list[CheckResult]:
        with patch(
            "punt_vox.voxd.output_health._resolve_music_dir", return_value=music_dir
        ):
            return _check_output(music_dir / "vox")[:-1]

    def test_present_dir_reports_nothing(self, tmp_path: Path) -> None:
        assert self._check(tmp_path) == []

    def test_missing_dir_names_the_command_that_creates_it(
        self, tmp_path: Path
    ) -> None:
        missing = tmp_path / "Music"
        results = self._check(missing)
        assert len(results) == 1
        assert results[0].symbol == "⚠"
        assert results[0].message == f"output music dir: absent — {_MUSIC_ON_ADVICE}"
        assert str(missing) not in results[0].message  # no absolute path

    def test_shell_output_override_keeps_the_row(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The installed voxd never sees this shell's VOX_OUTPUT_DIR, so albums
        # still land under the music dir and its absence is still worth a row.
        monkeypatch.setenv("VOX_OUTPUT_DIR", str(tmp_path / "albums"))
        assert len(self._check(tmp_path / "Music")) == 1

    def test_advised_command_exists(self) -> None:
        result = CliRunner().invoke(app, ["music", "on", "--help"])
        assert result.exit_code == 0, result.output

    def test_unavailable_music_dir_does_not_hide_output_verdict(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        with patch(
            "punt_vox.voxd.output_health._resolve_music_dir", side_effect=RuntimeError
        ):
            results = _check_output(tmp_path)
        assert [row.message for row in results] == ["output: writable"]
        assert "Could not check the music directory" in caplog.text


def _check_output(root: Path) -> list[CheckResult]:
    health = HealthStatus.from_wire(OutputHealth(root).probe())
    return OutputDirCheck(health).run()


class TestOutputDir:
    def test_writable_dir_reports_verdict_no_path(self, tmp_path: Path) -> None:
        results = _check_output(tmp_path)
        assert len(results) == 1
        assert results[0].passed is True
        assert results[0].message == "output: writable"
        assert str(tmp_path) not in results[0].message  # no absolute path

    def test_probe_leaves_existing_files_untouched(self, tmp_path: Path) -> None:
        existing = tmp_path / ".doctor_test"
        existing.write_text("user data")
        results = _check_output(tmp_path)
        assert results[0].passed is True
        assert existing.read_text() == "user data"
        assert sorted(p.name for p in tmp_path.iterdir()) == [".doctor_test"]

    def test_probe_does_not_write_through_symlink(self, tmp_path: Path) -> None:
        out_dir = tmp_path / "output"
        out_dir.mkdir()
        target = tmp_path / "elsewhere.txt"
        target.write_text("keep me")
        (out_dir / ".doctor_test").symlink_to(target)
        results = _check_output(out_dir)
        assert results[0].passed is True
        assert target.read_text() == "keep me"
        assert (out_dir / ".doctor_test").is_symlink()

    def test_unwritable_dir_reports_failure(self, tmp_path: Path) -> None:
        with patch(
            "punt_vox.voxd.output_health.tempfile.TemporaryFile", side_effect=OSError
        ):
            results = _check_output(tmp_path)
        assert results[0].passed is False
        assert results[0].message == "output: not writable — check permissions"

    def test_full_disk_reports_failure(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        with patch(
            "punt_vox.voxd.output_health.tempfile.TemporaryFile"
        ) as temporary_file:
            probe = temporary_file.return_value.__enter__.return_value
            probe.write.side_effect = OSError(errno.ENOSPC, "No space left on device")
            results = _check_output(tmp_path)
        temporary_file.assert_called_once_with(buffering=0, dir=tmp_path)
        assert results[0].passed is False
        assert results[0].message == "output: not writable — check permissions"
        assert "No space left on device" in caplog.text

    def test_missing_dir(self, tmp_path: Path) -> None:
        missing = tmp_path / "nonexistent"
        results = _check_output(missing)
        assert len(results) == 1
        assert results[0].passed is False
        assert results[0].symbol == "⚠"
        assert results[0].message == f"output: absent — {_MUSIC_ON_ADVICE}"
        assert str(missing) not in results[0].message  # no absolute path
