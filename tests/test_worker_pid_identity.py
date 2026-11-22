"""Regressions found by the final provider-boundary review."""

from __future__ import annotations

import signal
import subprocess
from unittest.mock import Mock, patch

import pytest

from punt_vox.voxd.workers.bootstrap import WorkerBootstrap
from punt_vox.voxd.workers.tmux import TmuxWorkerProvider


def test_stop_never_signals_a_recycled_process_group() -> None:
    def signal_group(_group: int, number: int) -> None:
        if number == 0:
            raise ProcessLookupError
        pytest.fail("a stored numeric group ID is not proof of ownership")

    with (
        patch.object(
            TmuxWorkerProvider,
            "_run",
            side_effect=[
                subprocess.CompletedProcess([], 0),
                subprocess.CompletedProcess([], 0, stdout="42"),
                subprocess.CompletedProcess([], 0),
            ],
        ),
        patch("punt_vox.voxd.workers.tmux.os.getpgid", return_value=42),
        patch("punt_vox.voxd.workers.tmux.os.getpgrp", return_value=1),
        patch("punt_vox.voxd.workers.tmux.os.killpg", side_effect=signal_group),
    ):
        TmuxWorkerProvider().stop("worker")


def test_bootstrap_installs_an_owner_local_hangup_handler() -> None:
    child = Mock()
    with (
        patch("punt_vox.voxd.workers.bootstrap.os.getpgrp", return_value=42),
        patch("punt_vox.voxd.workers.bootstrap.os.getpid", return_value=42),
        patch("punt_vox.voxd.workers.bootstrap.subprocess.Popen", return_value=child),
        patch("punt_vox.voxd.workers.bootstrap.os.killpg") as kill,
        patch("punt_vox.voxd.workers.bootstrap.signal.signal") as install,
    ):
        WorkerBootstrap._supervise(["harness"], {})
        assert install.call_count >= 1
        callback = install.call_args_list[0].args[1]
        callback(signal.SIGHUP, None)
        kill.assert_called_with(42, signal.SIGKILL)
