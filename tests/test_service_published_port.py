"""Tests for punt_vox.service.published_port -- the port voxd published, and whose."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from punt_vox.service.backend import PortProbeError
from punt_vox.service.process import DEFAULT_PORT, ProcessManager
from punt_vox.service.published_port import PublishedPort


def test_absent_file_publishes_nothing(tmp_path: Path) -> None:
    published = PublishedPort.at(tmp_path / "serve.port")
    assert published.port is None
    assert published.names_own_service(DEFAULT_PORT) is False


def test_own_file_naming_the_service_port_opens_the_gate(tmp_path: Path) -> None:
    (tmp_path / "serve.port").write_text("8421\n")
    published = PublishedPort.at(tmp_path / "serve.port")
    assert published.port == 8421
    assert published.names_own_service(8421) is True
    assert published.names_own_service(9000) is False


def test_a_file_naming_another_port_keeps_the_gate_closed(tmp_path: Path) -> None:
    (tmp_path / "serve.port").write_text("9000")
    assert PublishedPort.at(tmp_path / "serve.port").names_own_service(8421) is False


def test_an_unparseable_file_publishes_nothing(tmp_path: Path) -> None:
    (tmp_path / "serve.port").write_text("not a port")
    assert PublishedPort.at(tmp_path / "serve.port").port is None


def test_another_users_file_keeps_the_gate_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 0700 run dir makes this unreachable; the check still fails closed."""
    (tmp_path / "serve.port").write_text("8421")
    other = os.getuid() + 1
    monkeypatch.setattr(os, "getuid", lambda: other)
    assert PublishedPort.at(tmp_path / "serve.port").names_own_service(8421) is False


def test_an_unreadable_file_raises(tmp_path: Path) -> None:
    (tmp_path / "serve.port").write_bytes(b"\xff\xfe")
    with pytest.raises(PortProbeError, match="invalid UTF-8"):
        PublishedPort.at(tmp_path / "serve.port")


def test_process_manager_names_its_port_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ProcessManager, "_run_dir", staticmethod(lambda: tmp_path))
    (tmp_path / "serve.port").write_text("8421")
    assert PublishedPort.at(ProcessManager().port_file).names_own_service(8421)
