"""Tests for the daemon's album output probe."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from punt_vox.voxd import output_health
from punt_vox.voxd.output_health import OutputHealth


@pytest.mark.asyncio
async def test_timed_out_requests_share_the_in_flight_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    calls = 0
    release = asyncio.Event()
    finished = asyncio.Event()

    async def blocked_probe(_probe: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        await release.wait()
        finished.set()
        return {"output_dir": "writable"}

    monkeypatch.setattr(asyncio, "to_thread", blocked_probe)
    monkeypatch.setattr(output_health, "_PROBE_TIMEOUT_S", 0.01)
    probe = OutputHealth(tmp_path)

    try:
        assert await probe.probe_within_budget() == {}
        assert await probe.probe_within_budget() == {}
        monkeypatch.setattr(output_health, "_PROBE_TIMEOUT_S", 1.0)
        release.set()
        assert await probe.probe_within_budget() == {"output_dir": "writable"}
    finally:
        release.set()
        await asyncio.wait_for(finished.wait(), timeout=1.0)

    assert calls == 1
    assert "Timed out checking the album output directories" in caplog.text
    assert await probe.probe_within_budget() == {"output_dir": "writable"}
    assert calls == 2


def test_inaccessible_output_root_is_not_reported_as_absent(tmp_path: Path) -> None:
    with patch.object(Path, "stat", side_effect=PermissionError):
        verdict = OutputHealth(tmp_path).probe()

    assert verdict == {"output_dir": "not_writable", "music_dir_missing": False}


def test_missing_output_beneath_unwritable_ancestor_is_not_creatable(
    tmp_path: Path,
) -> None:
    root = tmp_path / "missing" / "albums"
    with patch(
        "punt_vox.voxd.output_health.tempfile.TemporaryFile",
        side_effect=PermissionError,
    ) as open_probe:
        assert OutputHealth(root).probe()["output_dir"] == "not_writable"

    open_probe.assert_called_once_with(buffering=0, dir=tmp_path)
    assert not root.parent.exists()


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

    assert OutputHealth(music / "vox").probe() == {
        "output_dir": "absent",
        "music_dir_missing": True,
    }
    assert OutputHealth(tmp_path).probe() == {
        "output_dir": "writable",
        "music_dir_missing": False,
    }
