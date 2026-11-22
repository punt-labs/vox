"""Type contracts can be imported without their daemon implementations."""

from __future__ import annotations

import subprocess
import sys

import pytest


@pytest.mark.subprocess
def test_hook_and_worker_contract_imports_are_lightweight() -> None:
    code = (
        "import sys;"
        "from punt_vox.voxd.hook_context_types import HookIngest;"
        "from punt_vox.voxd.workers.types import WorkerPayload;"
        "assert HookIngest.__module__ == 'punt_vox.voxd.hook_context_types';"
        "assert WorkerPayload.__module__ == 'punt_vox.voxd.workers.types';"
        "assert 'punt_vox.voxd.hook_context_handlers' not in sys.modules;"
        "assert 'punt_vox.voxd.workers.codec' not in sys.modules;"
        "assert 'starlette.websockets' not in sys.modules"
    )
    subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
