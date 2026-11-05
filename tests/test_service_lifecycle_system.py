"""Tests for punt_vox.service.lifecycle_system -- the observation verbs share."""

from __future__ import annotations

import pytest
from _lifecycle_fakes import WHEEL, FakeBackend, FakeClock, FakeProbe, FakeWorld

from punt_vox.service.backend import PortProbeError
from punt_vox.service.clock import Poll
from punt_vox.service.lifecycle_system import LifecycleSystem


def _system(world: FakeWorld) -> LifecycleSystem:
    return LifecycleSystem(
        FakeBackend(world), FakeProbe(world), Poll(FakeClock()), WHEEL
    )


def test_observe_probes_the_port_before_the_manager() -> None:
    """F0: a broken probe raises before the manager is asked anything."""
    world = FakeWorld(probe_error="cannot run fuser")
    with pytest.raises(PortProbeError):
        _system(world).observe()
    assert world.calls == []


def test_a_reported_main_process_counts_as_active() -> None:
    """F6: inactive but still reporting a main pid is a stopping unit."""
    world = FakeWorld()
    world.run_unit(100, on_port=False)
    world.unit_active = False
    system = _system(world)
    assert system.is_active() is True
    assert system.observe().active is True


def test_quiet_only_when_nothing_runs() -> None:
    world = FakeWorld()
    assert _system(world).observe().quiet is True
    world.foreign_listener = True
    assert _system(world).observe().quiet is False


def test_holder_pids_sorted() -> None:
    world = FakeWorld()
    world.spawn(300)
    world.spawn(200)
    assert _system(world).observe().holder_pids() == "200, 300"
