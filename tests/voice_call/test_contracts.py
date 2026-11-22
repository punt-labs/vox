from __future__ import annotations

import inspect

import pytest

from punt_vox.voxd.voice_call.contracts import ProjectAllowlist, WorkerControl
from punt_vox.voxd.workers.actor import WorkerActor
from punt_vox.voxd.workers.policy import WorkerRepoPolicy


@pytest.mark.parametrize(
    ("contract", "provider"),
    [(WorkerControl, WorkerActor), (ProjectAllowlist, WorkerRepoPolicy)],
)
def test_the_daemon_provides_what_the_voice_tools_call(
    contract: type, provider: type
) -> None:
    for name in ("start", "stop", "workers", "steer", "repos"):
        if hasattr(contract, name):
            expected = inspect.signature(getattr(contract, name))
            actual = inspect.signature(getattr(provider, name))
            assert (
                list(expected.parameters)
                == list(actual.parameters)[: len(expected.parameters)]
            )
