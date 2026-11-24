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
from punt_vox.voxd.workers.tmux_probe import TmuxProbe


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
        session = self._run("has-session", "-t", f"={handle}", check=False)
        if self._session_missing(handle, session):
            return False
        pane = self._run(
            "display-message", "-p", "-t", f"={handle}:", "#{pane_dead}", check=False
        )
        return TmuxProbe.pane_alive(pane)

    def _session_missing(
        self, handle: str, result: subprocess.CompletedProcess[str]
    ) -> bool:
        if result.returncode != 0 and result.stderr.strip() == "no current target":
            sessions = self._run("list-sessions", "-F", "#{session_name}", check=False)
            return (
                TmuxProbe.missing(sessions)
                or handle not in sessions.stdout.splitlines()
            )
        return TmuxProbe.missing(result)

    def stop(self, handle: str) -> None:
        """Kill the session and its pane process group.

        An absent session is a finished stop, whether it was gone at the
        probe or vanished between the probe and the kill. A session that is
        there but whose pane cannot be read or killed raises, so a rollback
        never records a possibly live pane as stopped and removes its worktree.

        Raises:
            RuntimeError: If a tmux command fails for a reason other than
                absence, or the session is there and cannot be killed.
        """
        if self._session_missing(
            handle, self._run("has-session", "-t", f"={handle}", check=False)
        ):
            return
        pane = self._run(
            "display-message", "-p", "-t", f"={handle}:", "#{pane_pid}", check=False
        )
        if self._ended(pane):
            return
        group = self._pane_group(pane.stdout)
        # A session gone by the kill still had a pane group a moment ago;
        # wait for it either way.
        self._ended(self._run("kill-session", "-t", f"={handle}", check=False))
        if group is not None:
            TmuxProbe.wait_for_exit(group)

    @staticmethod
    def _ended(result: subprocess.CompletedProcess[str]) -> bool:
        """Return whether the session vanished under this command; else raise."""
        if result.returncode == 0:
            return False
        try:
            return TmuxProbe.missing(result)
        except RuntimeError as exc:
            raise RuntimeError("Worker session could not be killed") from exc

    # PY-TS-14: None when the pane's process is already gone; nothing to wait for.
    @staticmethod
    def _pane_group(pane_pid: str) -> int | None:
        try:
            return os.getpgid(int(pane_pid.strip()))
        except ProcessLookupError:
            return None

    # PY-TS-14: transport relays the profile's open JSON snapshot schema unchanged.
    def progress(self, worker_id: str, profile: HarnessProfile) -> dict[str, object]:
        """Read the profile's progress source."""
        return profile.progress(worker_id)
