"""Stop the coding workers a verify-call run started, and report how that went.

``tools/verify_call.py`` places a real call whose agent starts one worker. The
agent is expected to stop it; this module checks that it did, stops any worker
the call started and left running, and never touches one it did not start.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Final, Self, cast, final

__all__ = ["Cleanup", "WorkerFleet"]

_TIMEOUT_S: Final = 30
_ENDED: Final = ("stopped", "exited")  # Registry phases with nothing to stop.
_STDERR_CHARS: Final = 200
_TASK_CHARS: Final = 80


@dataclass(frozen=True, slots=True)
class WorkerRow:
    """One worker as ``vox worker list`` reports it."""

    id: str
    state: str
    task: str

    @property
    def running(self) -> bool:
        """Return whether the worker still has something to stop."""
        return self.state not in _ENDED

    def describe(self) -> str:
        """Say why this worker was not stopped and how to stop it by hand."""
        task = self.task[:_TASK_CHARS] + ("..." if len(self.task) > _TASK_CHARS else "")
        return (
            f"worker {self.id} ({self.state}, task {task!r}) appeared during the "
            "call but this call's trace does not name it, so ownership is unproven "
            f"and it was not stopped; stop it by hand with: vox worker stop {self.id}"
        )


@dataclass(frozen=True, slots=True)
class Cleanup:
    """What became of the workers one call started, and of any it may not own."""

    started: tuple[str, ...]
    left_running: tuple[
        str, ...
    ]  # Still running after the call; this run stopped them.
    problems: tuple[str, ...]  # Each thing that could not be checked or stopped.
    unproven: tuple[
        WorkerRow, ...
    ] = ()  # Running, new during the call, not in its trace.
    ignored: tuple[WorkerRow, ...] = ()  # New and already ended: nothing left to stop.
    stopped: tuple[str, ...] = ()  # Left running, then stopped and confirmed ended.


@dataclass(frozen=True, slots=True)
class _StillRunning:
    """Workers a stop that exited 0 did not end, with the problems to report."""

    ids: frozenset[str]
    problems: list[str]


@final
class WorkerFleet:
    """The workers one call started, found and stopped through the ``vox`` CLI.

    Built from the workers that existed before the call, so a worker that
    appears during it and that the call's trace does not name is reported and
    never stopped: it may belong to another session.
    """

    __slots__ = ("_before",)
    _before: frozenset[str]

    def __new__(cls, before: frozenset[str]) -> Self:
        """Bind the IDs of the workers that existed before the call."""
        self = super().__new__(cls)
        self._before = before
        return self

    @classmethod
    def observe(cls) -> Self:
        """List the workers now, before the call is placed.

        Raises:
            ValueError: If the list could not be read, naming why and the CLI's stderr.
        """
        return cls(frozenset(row.id for row in cls._rows()))

    def sweep(self, started: tuple[str, ...]) -> Cleanup:
        """Stop each started worker still running; name every other new worker."""
        try:
            rows = self._rows()
        except ValueError as exc:
            return Cleanup(started, (), (str(exc),))
        new = [r for r in rows if r.id not in self._before and r.id not in started]
        unproven = tuple(r for r in new if r.running)
        ignored = tuple(r for r in new if not r.running)
        running = {row.id for row in rows if row.running}
        foreign = tuple(w for w in started if w in self._before)
        left = tuple(w for w in started if w in running and w not in foreign)
        problems = [
            f"worker {w} existed before the call, so it was not stopped"
            for w in foreign
        ]
        exited_zero: list[str] = []
        for worker in left:
            if fault := self._stop(worker):
                problems.append(fault)
            else:
                exited_zero.append(worker)
        confirmed: tuple[str, ...] = ()
        if exited_zero:
            still = self._still_running(tuple(exited_zero))
            problems += still.problems
            confirmed = tuple(w for w in exited_zero if w not in still.ids)
        return Cleanup(started, left, tuple(problems), unproven, ignored, confirmed)

    def _still_running(self, stopped: tuple[str, ...]) -> _StillRunning:
        """Return which workers a stop that exited 0 left running, and why."""
        try:
            running = {row.id for row in self._rows() if row.running}
        except ValueError as exc:
            return _StillRunning(frozenset(stopped), [str(exc)])
        ids = frozenset(w for w in stopped if w in running)
        return _StillRunning(
            ids,
            [f"worker {w} is still running after vox worker stop" for w in sorted(ids)],
        )

    @classmethod
    def _rows(cls) -> list[WorkerRow]:
        """Return every worker voxd reports.

        Raises:
            ValueError: If the list could not be read, naming why and the CLI's stderr.
        """
        done = cls._vox("list")
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
        entries = [cast("dict[str, object]", r) for r in rows if isinstance(r, dict)]
        if len(entries) != len(rows):
            raise ValueError("vox worker list printed not a list of workers")
        if not all(isinstance(e.get("id"), str) for e in entries):
            raise ValueError("vox worker list printed a worker with no id")
        return [
            WorkerRow(str(e["id"]), str(e.get("state", "")), str(e.get("task", "")))
            for e in entries
        ]

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
