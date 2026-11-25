"""Tests for the daemon's album output probe."""

from __future__ import annotations

import errno
import subprocess
import sys
import tempfile
from pathlib import Path
from threading import Event
from typing import assert_type
from unittest.mock import patch

import pytest

from punt_vox.types_health import OutputDiagnostics
from punt_vox.voxd import output_health
from punt_vox.voxd.output_health import OutputHealth


def test_stalled_probe_does_not_hold_process_open() -> None:
    script = """
import asyncio
from pathlib import Path
from threading import Event
from unittest.mock import patch
from punt_vox.voxd import output_health
from punt_vox.voxd.output_health import OutputHealth

entered = Event()

def stalled_probe(self):
    entered.set()
    Event().wait()

async def run():
    assert await OutputHealth(Path(".")).probe_within_budget() is None
    assert entered.is_set()

output_health._PROBE_TIMEOUT_S = 0.1
with patch.object(OutputHealth, "probe", stalled_probe):
    asyncio.run(run())
"""
    subprocess.run([sys.executable, "-c", script], check=True, timeout=4)


@pytest.mark.asyncio
async def test_timed_out_requests_share_the_in_flight_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    calls = 0
    release = Event()
    finished = Event()

    verdict: OutputDiagnostics = {"output_dir": "writable", "music_dir_missing": False}

    def blocked_probe(_probe: OutputHealth) -> OutputDiagnostics:
        nonlocal calls
        calls += 1
        assert release.wait(timeout=5.0)
        finished.set()
        return verdict

    monkeypatch.setattr(OutputHealth, "probe", blocked_probe)
    monkeypatch.setattr(output_health, "_PROBE_TIMEOUT_S", 0.01)
    probe = OutputHealth(tmp_path)

    try:
        assert await probe.probe_within_budget() is None
        assert await probe.probe_within_budget() is None
        monkeypatch.setattr(output_health, "_PROBE_TIMEOUT_S", 1.0)
        release.set()
        assert await probe.probe_within_budget() == verdict
    finally:
        release.set()
        assert finished.wait(timeout=1.0)

    assert calls == 1
    assert "Timed out checking the album output directories" in caplog.text
    assert await probe.probe_within_budget() == verdict
    assert calls == 2


def test_inaccessible_output_root_is_not_reported_as_absent(tmp_path: Path) -> None:
    with patch.object(Path, "stat", side_effect=PermissionError):
        verdict = OutputHealth(tmp_path).probe()

    assert verdict == {"output_dir": "not_writable", "music_dir_missing": False}


def test_missing_working_directory_blocks_relative_output_root() -> None:
    with patch.object(Path, "lstat", side_effect=FileNotFoundError):
        verdict = OutputHealth(Path("albums")).probe()

    assert verdict["output_dir"] == "not_writable"


def test_missing_output_beneath_unwritable_ancestor_is_not_creatable(
    tmp_path: Path,
) -> None:
    root = tmp_path / "missing" / "albums"
    with patch(
        "punt_vox.voxd.output_health.tempfile.TemporaryDirectory",
        side_effect=PermissionError,
    ) as open_probe:
        assert OutputHealth(root).probe()["output_dir"] == "not_writable"

    open_probe.assert_called_once_with(dir=tmp_path)
    assert not root.parent.exists()


def test_missing_output_requires_directory_creation_not_just_file_creation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "albums"
    with (
        patch.object(tempfile, "TemporaryDirectory", side_effect=PermissionError),
        patch.object(tempfile, "TemporaryFile") as open_probe,
    ):
        assert OutputHealth(root).probe()["output_dir"] == "not_writable"

    open_probe.assert_not_called()


def test_missing_output_requires_file_write_inside_created_directory(
    tmp_path: Path,
) -> None:
    root = tmp_path / "albums"

    def reject_file(**kwargs: object) -> None:
        directory = kwargs["dir"]
        assert isinstance(directory, str)
        probe_dir = Path(directory)
        assert probe_dir.parent == tmp_path
        assert probe_dir.is_dir()
        assert kwargs["buffering"] == 0
        raise PermissionError

    with patch.object(tempfile, "TemporaryFile", side_effect=reject_file):
        assert OutputHealth(root).probe()["output_dir"] == "not_writable"

    assert list(tmp_path.iterdir()) == []


def test_missing_output_is_not_writable_when_temporary_file_write_fails(
    tmp_path: Path,
) -> None:
    with patch.object(tempfile, "TemporaryFile") as open_probe:
        opened_file = open_probe.return_value.__enter__.return_value
        opened_file.write.side_effect = OSError(errno.ENOSPC, "no space left")

        assert OutputHealth(tmp_path / "albums").probe()["output_dir"] == "not_writable"

    opened_file.write.assert_called_once_with(b"ok")
    open_probe.return_value.__exit__.assert_called_once()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "obstruction", ["file", "dangling-link", "parent-file", "parent-dangling-link"]
)
def test_obstructed_output_root_is_not_reported_as_absent(
    tmp_path: Path, obstruction: str
) -> None:
    root = tmp_path / "output"
    if obstruction.endswith("dangling-link"):
        root.symlink_to(tmp_path / "missing")
    else:
        root.touch()
    if obstruction.startswith("parent-"):
        root /= "album"

    assert OutputHealth(root).probe()["output_dir"] == "not_writable"


def test_missing_output_beneath_valid_symlink_is_reported_as_absent(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)

    assert OutputHealth(link / "album").probe()["output_dir"] == "absent"


def test_probe_reports_only_verdicts_for_the_configured_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    music = tmp_path / "Music"
    monkeypatch.setattr(output_health, "_resolve_music_dir", lambda: music)

    assert assert_type(OutputHealth(music / "vox").probe(), OutputDiagnostics) == {
        "output_dir": "absent",
        "music_dir_missing": True,
    }
    assert OutputHealth(tmp_path).probe() == {
        "output_dir": "writable",
        "music_dir_missing": False,
    }
