"""A completed harness must not leave HUP-resistant descendants running."""

from __future__ import annotations

import os
import shutil
import signal
import sys
import time
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
def test_harness_exit_finishes_its_orphaned_process_group(tmp_path: Path) -> None:
    provider = TmuxWorkerProvider()
    worker_id = str(uuid4())
    handle = f"vox-test-{worker_id}"
    script = (
        "import os,signal,time;from pathlib import Path;"
        "signal.signal(signal.SIGHUP, signal.SIG_IGN);"
        "Path('descendant').write_text(str(os.getpid())+'\\n');"
        "time.sleep(120)"
    )
    parent = (
        "import subprocess,sys,time;from pathlib import Path;"
        f"subprocess.Popen([sys.executable,'-c',{script!r}]);"
        "\nwhile not Path('descendant').exists(): time.sleep(0.01)"
    )
    with patch.object(
        TrivialProfile, "argv", return_value=[sys.executable, "-c", parent]
    ):
        provider.start(
            WorkerLaunch(handle, worker_id, tmp_path, "task"), TrivialProfile()
        )
    pid = int(_until_file(tmp_path / "descendant", "\n"))
    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.01)
        pytest.fail("worker descendant survived harness exit")
    finally:
        provider.stop(handle)
        with suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)
