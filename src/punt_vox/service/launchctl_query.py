"""Bounded ``launchctl`` invocations for one job, and the queries that read them.

:class:`LaunchctlQuery` runs every ``launchctl`` command for one job with a
timeout and classifies the read-only ones -- ``print`` (registration and pid)
and ``list`` (loaded). Only exit 113 ("could not find service") means absent;
any other non-zero exit is a failed query and raises :class:`LaunchctlError`,
never read as "absent". The ``*_within`` queries take a budget for a bounded
wait: one the budget cuts short confirms nothing and names itself in the
:class:`~punt_vox.service.query_answer.QueryAnswer`.
"""

from __future__ import annotations

import re
import subprocess
from typing import TYPE_CHECKING, ClassVar, Self, final

from punt_vox.service.clock import Poll, SystemClock
from punt_vox.service.query_answer import QueryAnswer

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["LaunchctlError", "LaunchctlQuery"]

type Completed = subprocess.CompletedProcess[str]


@final
class LaunchctlError(RuntimeError):
    """A ``launchctl`` operation failed unrecoverably.

    Raised when a command cannot run or a query fails, when the job never
    leaves the GUI domain within the bound, or when ``bootstrap`` still fails
    after the single exit-5 retry. The caller translates this into a non-zero
    exit with a clear message.
    """

    __slots__ = ()


@final
class LaunchctlQuery:
    """Run ``launchctl`` for one job, bounded, and read what it reports."""

    __slots__ = ("_label", "_target")

    # launchctl print/list: "Could not find service" -- the only exit that
    # means the job is absent; every other non-zero exit is a failed query.
    _NOT_FOUND: ClassVar[int] = 113
    # A launchctl command that hangs must not hang the verb past its deadline.
    _COMMAND_TIMEOUT_S: ClassVar[float] = 10.0
    # The running pid in `launchctl print` output: a "pid = N" line.
    _PID_LINE: ClassVar[re.Pattern[str]] = re.compile(r"^\s*pid = (\S+)\s*$", re.M)

    _label: str
    _target: str

    def __new__(cls, label: str, target: str) -> Self:
        # *label* names the job for ``list``; *target* is its
        # ``gui/<uid>/<label>`` service target for ``print``.
        self = super().__new__(cls)
        self._label = label
        self._target = target
        return self

    def is_registered(self) -> bool:
        """Return True if launchd still has this job in the GUI domain."""
        return self._present(self._print())

    def pid(self) -> int | None:
        """Return the job's running pid, read from ``launchctl print``.

        ``None`` is the documented answer when the job is not registered or is
        registered with no running process. A failed query, or a ``pid = ...``
        line that is not a number, raises ``LaunchctlError``.
        """
        return self._pid_in(self._print())

    def is_loaded(self) -> bool:
        """Return True if ``launchctl list <label>`` reports the job loaded."""
        return self._present(self.run("list", self._label))

    def running_within(self, within_s: float) -> QueryAnswer:
        """Ask, inside *within_s*, whether the job runs (loaded, or has a pid).

        The two queries together take at most *within_s*: ``print`` gets only
        what ``list`` left.
        """
        poll = Poll(SystemClock())
        deadline = poll.now() + within_s
        loaded = self._answer(("list", self._label), within_s, self._present)
        if loaded.holds or loaded.timed_out:
            return loaded
        printed = ("print", self._target)
        remaining = deadline - poll.now()
        if remaining <= 0:
            return QueryAnswer.no_answer(f"launchctl {' '.join(printed)}")
        return self._answer(printed, remaining, self._has_pid)

    def left_within(self, within_s: float) -> QueryAnswer:
        """Ask ``launchctl print``, inside *within_s*, whether the job is gone."""
        return self._answer(("print", self._target), within_s, self._absent)

    def run(self, *args: str) -> Completed:
        """Run ``launchctl *args*``, bounded; raise if it cannot run in time."""
        try:
            return self._spawn(args, self._COMMAND_TIMEOUT_S)
        except subprocess.TimeoutExpired as exc:
            msg = f"launchctl {' '.join(args)} could not run: {exc}"
            raise LaunchctlError(msg) from exc

    def _print(self) -> Completed:
        """Run ``launchctl print`` on this job's service target."""
        return self.run("print", self._target)

    def _pid_in(self, result: Completed) -> int | None:
        """Return the pid a ``launchctl print`` *result* names.

        ``None`` is the documented answer when the job is absent or has no
        running process; a malformed ``pid = ...`` line raises.
        """
        if not self._present(result):
            return None
        found = self._PID_LINE.search(result.stdout)
        if found is None:
            return None
        if not found.group(1).isdecimal():
            msg = f"launchctl print {self._label}: malformed pid {found.group(1)!r}"
            raise LaunchctlError(msg)
        return int(found.group(1))

    def _has_pid(self, result: Completed) -> bool:
        """Return True when a ``launchctl print`` *result* names a running pid."""
        return self._pid_in(result) is not None

    def _absent(self, result: Completed) -> bool:
        """Return True when a print/list *result* says the job is gone."""
        return not self._present(result)

    def _present(self, result: Completed) -> bool:
        """Classify a print/list query: 0 present, 113 absent, else raise."""
        if result.returncode == 0:
            return True
        if result.returncode == self._NOT_FOUND:
            return False
        msg = (
            f"launchctl query of {self._label} failed "
            f"(exit {result.returncode}): {result.stderr.strip()}"
        )
        raise LaunchctlError(msg)

    def _answer(
        self,
        args: tuple[str, ...],
        within_s: float,
        read: Callable[[Completed], bool],
    ) -> QueryAnswer:
        """Run ``launchctl *args*`` within *within_s* and *read* its result.

        A query the budget cuts short confirms nothing and is named in the
        answer; one that fails outright raises ``LaunchctlError``.
        """
        try:
            result = self._spawn(args, within_s)
        except subprocess.TimeoutExpired:
            return QueryAnswer.no_answer(f"launchctl {' '.join(args)}")
        return QueryAnswer.of(holds=read(result))

    @staticmethod
    def _spawn(args: tuple[str, ...], within_s: float) -> Completed:
        """Run ``launchctl *args*`` for at most *within_s* seconds.

        A launchctl that cannot start raises ``LaunchctlError``; one that has
        not finished in time raises ``subprocess.TimeoutExpired`` so the caller
        decides whether running out of time is a failure or "not yet".
        """
        argv = ["launchctl", *args]
        try:
            return subprocess.run(  # noqa: S603
                argv,
                capture_output=True,
                text=True,
                check=False,
                timeout=within_s,
            )
        except OSError as exc:
            msg = f"{' '.join(argv)} could not run: {exc}"
            raise LaunchctlError(msg) from exc
