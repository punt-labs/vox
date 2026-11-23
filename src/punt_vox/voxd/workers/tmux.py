"""A dedicated tmux server hosts workers independently of voxd's lifetime."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from typing import final
from uuid import uuid4

from punt_vox.voxd.workers.command import WorkerCommand
from punt_vox.voxd.workers.contracts import HarnessProfile, WorkerLaunch
from punt_vox.voxd.workers.environment import WorkerEnvironment
from punt_vox.voxd.workers.steering_types import DecisionChoice


@final
class TmuxWorkerProvider:
    """Control one detached tmux session per worker."""

    __slots__ = ()

    @staticmethod
    def _run(
        *args: str, check: bool = True, input_text: str = ""
    ) -> subprocess.CompletedProcess[str]:
        return WorkerCommand(*args).run(check=check, input_text=input_text)

    def start(
        self,
        launch: WorkerLaunch,
        profile: HarnessProfile,
    ) -> None:
        """Start with an explicitly empty environment, not tmux's server env."""
        environment = WorkerEnvironment(launch.namespace)
        try:
            self._run(
                "new-session",
                "-d",
                "-s",
                launch.handle,
                "-c",
                str(launch.worktree),
                "-e",
                f"VOX_WORKER_OWNER={launch.namespace}:{launch.worker_id}",
                "--",
                "env",
                "-i",
                sys.executable,
                "-E",
                "-P",
                "-m",
                "punt_vox.voxd.workers.bootstrap",
                str(environment.address),
                *profile.argv(launch.worker_id, launch.task),
            )
            environment.send(
                {
                    **profile.environment(os.environ),
                    "VOX_WORKER_SESSION_ID": launch.worker_id,
                }
            )
        finally:
            environment.close()

    def steer(
        self, handle: str, words: str, *, urgent: bool, profile: HarnessProfile
    ) -> None:
        """Send literal words followed by the harness's submission key."""
        if urgent:
            self._run("send-keys", "-t", f"={handle}:", profile.interrupt_key)
            time.sleep(profile.settle_delay)
        self._paste(handle, words)
        time.sleep(profile.settle_delay)
        self._run("send-keys", "-t", f"={handle}:", profile.submit_key)

    def _paste(self, handle: str, words: str) -> None:
        buffer = f"vox-steer-{uuid4()}"
        self._run("load-buffer", "-b", buffer, "-", input_text=words)
        try:
            self._run("paste-buffer", "-b", buffer, "-t", f"={handle}:")
        finally:
            self._run("delete-buffer", "-b", buffer, check=False)

    def decide(
        self, handle: str, choice: DecisionChoice, *, profile: HarnessProfile
    ) -> None:
        """Send the selected permission keys without pasting or submitting text."""
        self._run("send-keys", "-t", f"={handle}:", *profile.decision_keys(choice))

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
                self._finish(group)

    @staticmethod
    def _finish(group: int) -> None:
        if group == os.getpgrp():
            raise RuntimeError("Worker pane must have its own process group")
        deadline = time.monotonic() + 3
        while True:
            try:
                os.killpg(group, 0)
            except ProcessLookupError:
                return
            if time.monotonic() >= deadline:
                raise RuntimeError("Worker pane did not exit after stop")
            time.sleep(0.01)

    # PY-TS-14: transport relays the profile's open JSON snapshot schema unchanged.
    def progress(self, worker_id: str, profile: HarnessProfile) -> dict[str, object]:
        """Read the profile's progress source."""
        return profile.progress(worker_id)
