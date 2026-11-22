"""Run tmux control clients without tying their server to the daemon cgroup."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Self, final

from punt_vox.process_env import ProcessEnv

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

    def run(self, *, check: bool) -> subprocess.CompletedProcess[str]:
        """Run a bounded client; a missing user manager fails launch closed."""
        argv = ["tmux", "-L", "vox-workers", "-f", "/dev/null", *self._args]
        environment = {k: v for k, v in os.environ.items() if ProcessEnv.admits(k)}
        try:
            if self._scoped():
                environment.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
                argv[:0] = self._scope(environment)
            return subprocess.run(
                argv,
                env=environment,
                check=check,
                capture_output=True,
                text=True,
                timeout=15,
            )
        except subprocess.SubprocessError:
            raise RuntimeError("Worker tmux operation failed") from None

    @staticmethod
    def _scope(environment: dict[str, str]) -> list[str]:
        options = subprocess.run(
            ["systemd-run", "--help"],
            env=environment,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        argv = ["systemd-run", "--user", "--scope", "--quiet"]
        if "--expand-environment=" in options.stdout:
            argv.append("--expand-environment=no")
        return [*argv, "--"]
