"""Tests for the plugin hook CLI boundary."""

from __future__ import annotations

from typing import TYPE_CHECKING

from typer.testing import CliRunner

from punt_vox.__main__ import app

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_hook_log_dir_prints_the_shared_home_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home with spaces"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("VOX_LOG_DIR", str(tmp_path / "other logs"))

    result = CliRunner().invoke(app, ["hook", "_log-dir"])

    assert result.exit_code == 0, result.output
    assert result.stdout == f"{home}/.punt-labs/vox/logs\n"
    assert result.stderr == ""
