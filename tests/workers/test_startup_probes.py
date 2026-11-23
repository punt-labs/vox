"""Startup command budgets propagate to concurrent probes."""

import pytest

from punt_vox.voxd.workers.startup_probes import StartupProbes


def test_startup_command_budget_propagates_and_resets() -> None:
    assert StartupProbes.timeout(15.0) == 15.0
    timeouts = StartupProbes.run(
        lambda: StartupProbes.completed([15.0, 0.01], StartupProbes.timeout)
    )
    assert 0 < timeouts[0] <= 0.4
    assert timeouts[1] == pytest.approx(0.01)
    assert StartupProbes.timeout(15.0) == 15.0
