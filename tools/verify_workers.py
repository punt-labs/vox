"""Stop the coding workers a verify-call run started, and report how that went.

``tools/verify_call.py`` places a real call whose agent starts one worker. The
agent is expected to stop it; this module checks that it did, stops any worker
the call started and left running, and never touches one it did not start.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Final, Self, final

__all__ = ["Cleanup", "WorkerFleet"]

_TIMEOUT_S: Final = 30
_ENDED: Final = ("stopped", "exited")  # Registry phases with nothing to stop.
_STDERR_CHARS: Final = 200


@dataclass(frozen=True, slots=True)
class Cleanup:
    """What became of the workers one call started."""

    started: tuple[str, ...]
    left_running: tuple[
        str, ...
    ]  # Still running after the call; this run stopped them.
    problems: tuple[str, ...]  # Each thing that could not be checked or stopped.


@final
class WorkerFleet:
    """The workers one call started, found and stopped through the ``vox`` CLI."""

    __slots__ = ("_started",)
    _started: tuple[str, ...]

    def __new__(cls, started: tuple[str, ...]) -> Self:
        """Bind the worker IDs the call's own trace says it started."""
        self = super().__new__(cls)
        self._started = started
        return self

    def sweep(self) -> Cleanup:
        """Stop each started worker still running; report what could not be done."""
        if not self._started:
            return Cleanup((), (), ())
        try:
            left = tuple(w for w in self._started if w in self._running())
        except ValueError as exc:
            return Cleanup(self._started, (), (str(exc),))
        problems = [fault for worker in left if (fault := self._stop(worker))]
        if left and not problems:
            problems = self._still_running(left)
        return Cleanup(self._started, left, tuple(problems))

    def _still_running(self, stopped: tuple[str, ...]) -> list[str]:
        """Return a problem for each worker a successful stop left running."""
        try:
            running = self._running()
        except ValueError as exc:
            return [str(exc)]
        return [
            f"worker {w} is still running after vox worker stop"
            for w in stopped
            if w in running
        ]

    def _running(self) -> set[str]:
        """Return the IDs of workers voxd reports as running.

        Raises:
            ValueError: If the list could not be read, naming why and the CLI's stderr.
        """
        done = self._vox("list")
        if isinstance(done, str):
            raise ValueError(f"vox worker list failed: {done}")
        try:
            rows: object = json.loads(done.stdout)
        except json.JSONDecodeError as exc:
            msg = f"vox worker list was unreadable: {done.stdout[:_STDERR_CHARS]!r}"
            raise ValueError(msg) from exc
        if not isinstance(rows, list):
            raise ValueError(
                f"vox worker list printed not a list: {type(rows).__name__}"
            )
        entries = [row for row in rows if isinstance(row, dict)]
        if len(entries) != len(rows):
            raise ValueError("vox worker list printed not a list of workers")
        return {str(row["id"]) for row in entries if row.get("state") not in _ENDED}

    def _stop(self, worker_id: str) -> str:
        """Stop one worker; return what went wrong, or '' when it stopped."""
        done = self._vox("stop", worker_id)
        if isinstance(done, str):
            return f"vox worker stop {worker_id} failed: {done}"
        return ""

    @staticmethod
    def _vox(*words: str) -> subprocess.CompletedProcess[str] | str:
        """Run ``vox worker <words>`` under a timeout; return failure text on error."""
        command = ["timeout", str(_TIMEOUT_S), "vox", "worker", *words]
        try:
            done = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=_TIMEOUT_S + 5,
            )
        except subprocess.TimeoutExpired:
            return f"timed out after {_TIMEOUT_S} s"
        except OSError as exc:
            return type(exc).__name__
        if done.returncode == 124:  # The timeout command's own exit status.
            return f"timed out after {_TIMEOUT_S} s"
        if done.returncode != 0:
            detail = done.stderr.strip()[:_STDERR_CHARS] or "no stderr"
            return f"exit {done.returncode}: {detail}"
        return done
