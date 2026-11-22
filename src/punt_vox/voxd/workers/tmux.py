"""A dedicated tmux server hosts workers independently of voxd's lifetime."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from contextlib import suppress
from pathlib import Path
from typing import final

from punt_vox.voxd.workers.contracts import HarnessProfile


@final
class TmuxWorkerProvider:
    """Control one detached tmux session per worker."""

    __slots__ = ()

    @staticmethod
    def _run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                ["tmux", "-L", "vox-workers", "-f", "/dev/null", *args],
                check=check,
                capture_output=True,
                text=True,
                timeout=15,
            )
        except subprocess.SubprocessError:
            raise RuntimeError("Worker tmux operation failed") from None

    def start(
        self,
        handle: str,
        worker_id: str,
        worktree: Path,
        task: str,
        profile: HarnessProfile,
    ) -> None:
        """Start with an explicitly empty environment, not tmux's server env."""
        env = [f"{k}={v}" for k, v in profile.environment(os.environ).items()]
        self._run(
            "new-session",
            "-d",
            "-s",
            handle,
            "-c",
            str(worktree),
            "--",
            "env",
            "-i",
            *env,
            *profile.argv(worker_id, task),
        )

    def steer(
        self, handle: str, words: str, *, urgent: bool, profile: HarnessProfile
    ) -> None:
        """Send literal words followed by the harness's submission key."""
        if urgent:
            self._run("send-keys", "-t", f"={handle}:", profile.interrupt_key)
        self._run("send-keys", "-t", f"={handle}:", "-l", "--", words)
        self._run("send-keys", "-t", f"={handle}:", profile.submit_key)

    def status(self, handle: str) -> bool:
        """Report whether the session is still alive."""
        if self._run("has-session", "-t", f"={handle}", check=False).returncode != 0:
            return False
        pane = self._run(
            "display-message", "-p", "-t", f"={handle}:", "#{pane_dead}", check=False
        )
        return pane.returncode == 0 and pane.stdout.strip() == "0"

    def stop(self, handle: str) -> None:
        """Kill the session and its pane process group."""
        if self._run("has-session", "-t", f"={handle}", check=False).returncode == 0:
            pane = self._run(
                "display-message", "-p", "-t", f"={handle}:", "#{pane_pid}", check=False
            )
            if pane.returncode != 0:
                return
            pid = int(pane.stdout.strip())
            try:
                group = os.getpgid(pid)
            except ProcessLookupError:
                group = None
            self._run("kill-session", "-t", f"={handle}", check=False)
            if group is not None:
                self._finish(pid, group)

    @staticmethod
    def _finish(pid: int, group: int) -> None:
        if group == os.getpgrp():
            raise RuntimeError("Worker pane must have its own process group")
        with suppress(ProcessLookupError):
            os.killpg(group, signal.SIGKILL)
        deadline = time.monotonic() + 3
        while True:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            if time.monotonic() >= deadline:
                raise RuntimeError("Worker pane did not exit after stop")
            time.sleep(0.01)

    def progress(self, worker_id: str, profile: HarnessProfile) -> dict[str, object]:
        """Read the profile's progress source."""
        return profile.progress(worker_id)
