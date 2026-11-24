"""Tests for projecting step admission from pending source changes."""

from __future__ import annotations

from unittest.mock import create_autospec

from punt_vox.voxd.programs.lifecycle_signal import TurnOff, TurnOn
from punt_vox.voxd.programs.playback_signal import StepForward
from punt_vox.voxd.programs.step_admission import StepAdmission


def test_last_source_change_decides_until_consumed() -> None:
    admission = StepAdmission()
    start, restart = TurnOn(), TurnOn()
    stop = create_autospec(TurnOff, instance=True)
    step = StepForward()

    assert admission.admit(start, active=False)
    assert admission.admit(stop, active=False)
    assert not admission.admit(step, active=False)
    admission.consume(start)
    assert not admission.admit(step, active=True)
    assert admission.admit(restart, active=True)
    admission.consume(stop)
    assert admission.admit(step, active=False)
    admission.consume(restart)
    assert not admission.admit(step, active=False)
    assert admission.admit(step, active=True)
