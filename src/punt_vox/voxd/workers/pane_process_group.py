"""Observe the OS process group remaining after a tmux pane stops."""

from __future__ import annotations

import os
import platform
import subprocess
import time
from typing import Self, final

from punt_vox.voxd.workers.startup_probes import StartupProbes


@final
class PaneProcessGroup:
    """Wait for a separate worker group without signaling it."""

    __slots__ = ("_group",)
    _group: int

    def __new__(cls, group: int) -> Self:
        """Bind the process group to observe."""
        self = super().__new__(cls)
        self._group = group
        return self

    def wait_for_exit(self) -> None:
        """Wait within the stop budget for the group's processes to exit."""
        if self._group == os.getpgrp():
            raise RuntimeError("Worker pane must have its own process group")
        deadline = time.monotonic() + StartupProbes.timeout(3)
        while not self._exited(deadline):
            if time.monotonic() >= deadline:
                raise RuntimeError("Worker pane did not exit after stop")
            time.sleep(0.01)

    def _exited(self, deadline: float) -> bool:
        try:
            os.killpg(self._group, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            # Darwin also returns EPERM for groups made up only of zombies.
            remaining = deadline - time.monotonic()
            if (
                platform.system() != "Darwin"
                or remaining <= 0
                or self._has_live_members(remaining)
            ):
                raise
            return True
        return False

    def _has_live_members(self, timeout: float) -> bool:
        rows = subprocess.check_output(
            ["/bin/ps", "-ax", "-o", "pgid=,stat="], text=True, timeout=timeout
        ).splitlines()
        group = str(self._group)
        for row in rows:
            pgid, state = row.split()
            if pgid == group and not state.startswith("Z"):
                return True
        return False
