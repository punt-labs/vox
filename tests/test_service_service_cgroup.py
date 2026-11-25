"""Tests for punt_vox.service.service_cgroup -- is a pid in voxd.service's cgroup."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from _lifecycle_fakes import probe_over

from punt_vox.service.backend import PortProbeError
from punt_vox.service.service_cgroup import ServiceCgroup


def test_service_owned_reads_the_cgroup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pid in voxd.service's cgroup is the manager's, not unmanaged."""
    monkeypatch.setattr(ServiceCgroup, "_PROC", tmp_path)
    (tmp_path / "42").mkdir()
    (tmp_path / "42" / "cgroup").write_text("0::/system.slice/voxd.service\n")
    (tmp_path / "43").mkdir()
    (tmp_path / "43" / "cgroup").write_text("0::/user.slice/user-1000.slice\n")
    probe = probe_over(MagicMock())
    assert probe.service_owned(42) is True
    assert probe.service_owned(43) is False
    assert probe.service_owned(44) is False  # gone, or no /proc (macOS)


@pytest.mark.parametrize(
    ("line", "owned"),
    [
        ("0::/system.slice/voxd.service", True),
        ("0::/system.slice/voxd.service/sub", True),
        ("1:name=systemd:/system.slice/voxd.service", True),
        (
            "0::/user.slice/user-1000.slice/user@1000.service/app.slice/voxd.service",
            False,
        ),
        ("0::/system.slice/voxd.service-other", False),
    ],
)
def test_service_owned_matches_the_system_unit_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, line: str, owned: bool
) -> None:
    """Only the system unit vox installs counts, not a user-scope voxd."""
    monkeypatch.setattr(ServiceCgroup, "_PROC", tmp_path)
    (tmp_path / "42").mkdir()
    (tmp_path / "42" / "cgroup").write_text(line + "\n")
    assert probe_over(MagicMock()).service_owned(42) is owned


def test_an_unreadable_cgroup_file_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only "gone" reads as not counted; any other read failure propagates."""
    monkeypatch.setattr(ServiceCgroup, "_PROC", tmp_path)
    (tmp_path / "42").mkdir()
    (tmp_path / "42" / "cgroup").mkdir()  # a directory: IsADirectoryError
    with pytest.raises(PortProbeError, match="cannot read"):
        ServiceCgroup.counts(42)
