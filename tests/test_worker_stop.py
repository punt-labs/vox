"""A successful stop must leave no live pane, including HUP-resistant harnesses."""

from __future__ import annotations

import os
import shutil
import signal
from contextlib import suppress
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from test_workers import TrivialProfile
from test_workers_subprocess import _until_file

from punt_vox.voxd.workers.contracts import WorkerLaunch
from punt_vox.voxd.workers.tmux import TmuxWorkerProvider

pytestmark = pytest.mark.subprocess


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is required")
def test_stop_waits_for_hup_resistant_pane(tmp_path: Path) -> None:
    provider = TmuxWorkerProvider()
    worker_id = str(uuid4())
    handle = f"vox-test-{worker_id}"
    argv = ["/bin/sh", "-c", "trap '' HUP; echo $$ > leader; exec sleep 120"]
    with patch.object(TrivialProfile, "argv", return_value=argv):
        provider.start(
            WorkerLaunch(handle, worker_id, tmp_path, "task"), TrivialProfile()
        )
    pid = int(_until_file(tmp_path / "leader", "\n"))
    try:
        provider.stop(handle)
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    finally:
        provider.stop(handle)
        with suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)
