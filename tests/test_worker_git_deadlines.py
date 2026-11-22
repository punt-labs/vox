"""A stalled repository command must release the serialized worker actor."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from punt_vox.config import ConfigStore
from punt_vox.voxd.workers.policy import WorkerRepoPolicy


@pytest.mark.parametrize("operation", ["resolve", "create"])
def test_worker_git_commands_have_a_bounded_deadline(
    tmp_path: Path, operation: str
) -> None:
    config = Mock(spec=ConfigStore)
    config.read_field.return_value = repr([str(tmp_path)])
    policy = WorkerRepoPolicy(config)
    with patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess([], 0, str(tmp_path), ""),
    ) as run:
        if operation == "resolve":
            policy.resolve(str(tmp_path))
        else:
            policy.create(tmp_path, tmp_path / "worktree", "worker")
    timeout = run.call_args.kwargs.get("timeout")
    assert timeout is not None, "worker Git subprocess has no deadline"
    assert 0 < timeout < 30
