"""Distinguish confirmed tmux session loss from unavailable status."""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING, final

from punt_vox.voxd.workers.startup_probes import StartupProbes

if TYPE_CHECKING:
    from subprocess import CompletedProcess


@final
class TmuxProbe:
    """Classify checked tmux liveness responses."""

    __slots__ = ()

    @staticmethod
    def missing(result: CompletedProcess[str]) -> bool:
        """Identify an absent session or absent local server socket."""
        if result.returncode == 0:
            return False
        error = result.stderr.strip()
        if error.startswith(
            ("can't find session:", "no such session", "no server running on ")
        ):
            return True
        if error.startswith("error connecting to ") and error.endswith(
            "(No such file or directory)"
        ):
            return True
        raise RuntimeError("Worker tmux liveness probe failed")

    @classmethod
    def pane_alive(cls, result: CompletedProcess[str]) -> bool:
        """Return liveness only when tmux reports a definite pane state."""
        if cls.missing(result):
            return False
        match result.stdout.strip():
            case "0":
                return True
            case "1":
                return False
            case _:
                raise RuntimeError("Worker tmux liveness probe returned no pane state")

    @staticmethod
    def wait_for_exit(group: int) -> None:
        """Wait for the worker pane's separate process group to disappear."""
        if group == os.getpgrp():
            raise RuntimeError("Worker pane must have its own process group")
        deadline = time.monotonic() + StartupProbes.timeout(3)
        while True:
            try:
                os.killpg(group, 0)
            except ProcessLookupError:
                return
            if time.monotonic() >= deadline:
                raise RuntimeError("Worker pane did not exit after stop")
            time.sleep(0.01)
