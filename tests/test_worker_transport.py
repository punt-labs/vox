"""Real transport boundaries keep credentials out of process arguments."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from test_workers import TrivialProfile
from test_workers_subprocess import _until_file

from punt_vox.voxd.workers.contracts import WorkerLaunch
from punt_vox.voxd.workers.tmux import TmuxWorkerProvider


@pytest.mark.subprocess
def test_worker_environment_values_never_enter_tmux_argv(tmp_path: Path) -> None:
    provider = TmuxWorkerProvider()
    worker_id = str(uuid4())
    handle = f"vox-test-{worker_id}"
    environment = {"PATH": os.defpath, "CLAUDE_CODE_OAUTH_TOKEN": "fake-private-token"}
    original = TmuxWorkerProvider._run

    def run(*args: str, **kwargs: bool) -> object:
        assert all(value not in arg for value in environment.values() for arg in args)
        return original(*args, **kwargs)

    try:
        with (
            patch.object(TrivialProfile, "environment", return_value=environment),
            patch.object(TmuxWorkerProvider, "_run", side_effect=run),
        ):
            provider.start(
                WorkerLaunch(handle, worker_id, tmp_path, "task"), TrivialProfile()
            )
        contents = _until_file(tmp_path / "environment", "fake-private-token")
        assert "CLAUDE_CODE_OAUTH_TOKEN=fake-private-token" in contents
        assert "PATH=" in contents
    finally:
        provider.stop(handle)
