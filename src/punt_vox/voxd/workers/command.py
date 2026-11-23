"""Run tmux control clients without tying their server to the daemon cgroup."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Self, final

from punt_vox.process_env import ProcessEnv
from punt_vox.voxd.workers.startup_probes import StartupProbes

__all__ = ["WorkerCommand"]


@final
class WorkerCommand:
    """Scope a new server separately while retaining normal daemon child cleanup."""

    __slots__ = ("_args",)
    _args: tuple[str, ...]

    def __new__(cls, *args: str) -> Self:
        """Bind one tmux command without any credential values."""
        self = super().__new__(cls)
        self._args = args
        return self

    def _scoped(self) -> bool:
        if self._args[0] != "new-session" or sys.platform != "linux":
            return False
        try:
            return ".service" in Path("/proc/self/cgroup").read_text()
        except OSError:
            raise RuntimeError("Cannot identify the worker service cgroup") from None

    def run(
        self, *, check: bool, input_text: str = ""
    ) -> subprocess.CompletedProcess[str]:
        """Run a client; service scopes require systemd 254+ and a user manager."""
        argv = ["tmux", "-L", "vox-workers", "-f", "/dev/null", *self._args]
        environment = {k: v for k, v in os.environ.items() if ProcessEnv.admits(k)}
        scoped = self._scoped()
        try:
            if scoped:
                environment.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
                argv[:0] = [
                    "systemd-run",
                    "--user",
                    "--scope",
                    "--quiet",
                    "--expand-environment=no",
                    "--",
                ]
            return subprocess.run(
                argv,
                env=environment,
                check=check,
                capture_output=True,
                text=True,
                input=input_text,
                timeout=StartupProbes.timeout(15),
            )
        except subprocess.SubprocessError:
            if scoped:
                raise RuntimeError(
                    "Worker scope launch failed: requires systemd 254+ with "
                    "--expand-environment=no and a running user manager"
                ) from None
            raise RuntimeError("Worker tmux operation failed") from None
