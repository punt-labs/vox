"""Tests for punt_vox.service.backend -- the typed failures the lifecycle reads."""

from __future__ import annotations

from _lifecycle_fakes import FakeBackend, FakeProbe, FakeWorld

from punt_vox.service.backend import (
    BackendCommandError,
    LifecycleProbe,
    PortProbeError,
    PrivilegeDeniedError,
    ServiceBackend,
)


def test_backend_command_error_carries_argv_and_code() -> None:
    exc = BackendCommandError.of(["systemctl", "stop", "voxd"], 5, "not loaded")
    assert exc.argv == ("systemctl", "stop", "voxd")
    assert exc.code == 5
    assert str(exc) == "systemctl stop voxd exited 5: not loaded"


def test_error_hierarchy_matches_the_callers_catches() -> None:
    """``vox install`` catches OSError; the refusal must be one."""
    assert issubclass(PrivilegeDeniedError, PermissionError)
    assert issubclass(PortProbeError, RuntimeError)
    assert issubclass(BackendCommandError, RuntimeError)


def test_fakes_satisfy_the_protocols() -> None:
    """The fakes stand in structurally, so the lifecycle tests drive real seams."""
    world = FakeWorld()
    backend: ServiceBackend = FakeBackend(world)
    probe: LifecycleProbe = FakeProbe(world)
    assert backend.is_installed() is True
    assert probe.voxd_processes() == frozenset()
