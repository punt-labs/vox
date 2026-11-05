"""In-memory stand-ins for the daemon lifecycle's collaborators.

One :class:`FakeWorld` holds the state a real host would: the unit, the
manager's main process, the voxd processes and which of them holds the port,
an invisible listener, and who answers health. :class:`FakeBackend` and
:class:`FakeProbe` read and change that one world, so a test sets the scene
once and both seams agree. :class:`FakeClock` is a fake monotonic clock whose
``sleep`` fires scheduled world events, so a poll runs in no real time.
:class:`FakeSudo` scripts exit codes per argv for ``PrivilegedRun``.

Every fake records what it was asked to do, so a test can assert on the
modeled properties: which processes were signalled, where a token went, and
whether the backend was touched at all.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Self, final

from punt_vox.service.backend import (
    KILL_GRACE_FLOOR_S,
    BackendCommandError,
    PortProbeError,
    PrivilegeDeniedError,
)
from punt_vox.service.clock import Poll
from punt_vox.service.lifecycle import DaemonLifecycle
from punt_vox.service.lifecycle_report import LifecycleOutcome
from punt_vox.service.lifecycle_system import LifecycleSystem
from punt_vox.service.port_file_cleanup import PortFileCleanup
from punt_vox.service.termination import Termination
from punt_vox.types_health import HealthStatus

if TYPE_CHECKING:
    from punt_vox.service.backend import ProcessHandle
    from punt_vox.service.lifecycle_report import LifecycleReport

WHEEL = "5.0.6"
STALE = "5.0.5"


@final
class FakeProcess:
    """One voxd process: alive or not, on the port or not, killable or not."""

    __slots__ = (
        "_exiting",
        "_on_port",
        "_pid",
        "_refuses",
        "_slow_exit",
        "_survives",
        "_world",
        "alive",
        "version",
    )

    _world: FakeWorld
    _pid: int
    _on_port: bool
    _survives: bool
    _slow_exit: bool
    _refuses: bool
    _exiting: bool
    alive: bool
    version: str

    def __new__(
        cls,
        world: FakeWorld,
        pid: int,
        *,
        version: str = WHEEL,
        survives: bool = False,
        slow_exit: bool = False,
        refuses: bool = False,
    ) -> Self:
        self = super().__new__(cls)
        self._world = world
        self._pid = pid
        self._on_port = False
        self._survives = survives
        self._slow_exit = slow_exit
        self._refuses = refuses
        self._exiting = False
        self.alive = True
        self.version = version
        return self

    @property
    def pid(self) -> int:
        return self._pid

    @property
    def on_port(self) -> bool:
        self._settle()
        return self.alive and self._on_port

    def _settle(self) -> None:
        """A slow exiter that outlived its terminate() is gone by the next look."""
        if self._exiting:
            self._exiting = False
            self.exit()

    def bind(self) -> None:
        self._on_port = True

    def unbind(self) -> None:
        self._on_port = False

    def exit(self) -> None:
        self.alive = False
        self._on_port = False

    def exited(self) -> bool:
        self._settle()
        return not self.alive

    def terminate(self, within_s: float) -> Termination:
        """Exit at once, or -- a survivor or slow exiter -- block the whole
        budget as the real handle would, so the fake clock shows what the kill
        cost. A slow exiter is still alive when terminate() returns and gone
        by the next look. A process that refuses the signal is untouched.

        The outcome follows the real handles: below the grace floor only
        SIGTERM is sent.
        """
        world = self._world
        world.signalled.append(self._pid)
        world.budgets.append(within_s)
        if world.on_signal is not None:
            world.on_signal(self._pid)
        if self._refuses:
            return Termination.REFUSED
        if not (self._survives or self._slow_exit):
            self.exit()
            return Termination.EXITED
        if world.clock is not None:
            world.clock.advance(within_s)
        self._exiting = self._slow_exit
        if within_s < KILL_GRACE_FLOOR_S:
            return Termination.TERM_ONLY
        return Termination.SURVIVED


@final
class FakeHandle:
    """A fresh handle per probe call, equal to every other handle on its process.

    The real probe opens new handles on every tick, so a fake that hands back
    the same object would hide a lifecycle that compares handles by identity.
    """

    __slots__ = ("_proc",)

    _proc: FakeProcess

    def __new__(cls, proc: FakeProcess) -> Self:
        self = super().__new__(cls)
        self._proc = proc
        return self

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, FakeHandle):
            return NotImplemented
        return self._proc is other._proc

    def __hash__(self) -> int:
        return hash(id(self._proc))

    @property
    def pid(self) -> int:
        return self._proc.pid

    def exited(self) -> bool:
        return self._proc.exited()

    def terminate(self, within_s: float) -> Termination:
        return self._proc.terminate(within_s)


@final
@dataclass(slots=True)
class FakeWorld:
    """The whole host the fakes share."""

    installed: bool = True
    unit_active: bool = False
    mainpid: int | None = None
    procs: dict[int, FakeProcess] = field(default_factory=dict)
    foreign_listener: bool = False
    off_port: frozenset[int] = frozenset()
    token_written: bool = True
    probe_error: str = ""
    # scripted manager responses
    stop_error: Exception | None = None
    start_error: Exception | None = None
    stop_takes_effect: bool = True
    start_takes_effect: bool = True
    start_version: str = WHEEL
    start_binds: bool = True
    next_pid: int = 500
    # processes systemd counts in voxd.service's cgroup
    service_pids: set[int] = field(default_factory=set)
    # serve.port as the run directory holds it; None: no file
    port_file: int | None = None
    # serve.port holds unparseable content (a crash leftover); port_file unused
    port_file_stale: bool = False
    # called with the pid each time a process is signalled
    on_signal: Callable[[int], None] | None = None
    # acts once, just before the probe clears serve.port
    before_clear: Callable[[], None] | None = None
    # a manager query that fails (systemctl show, launchctl print)
    query_error: Exception | None = None
    # the cause the last gated health request failed with
    health_error: str = "connection refused"
    # an actor other than vox: replaces the manager's stop, acts between the
    # stop and start halves of restart (at the second observation), or acts
    # once, just before the probe next lists the port holders
    on_stop: Callable[[], None] | None = None
    on_start: Callable[[], None] | None = None
    between: Callable[[], None] | None = None
    before_holders: Callable[[], None] | None = None
    # the clock a lifecycle over this world polls on (set by ``lifecycle``)
    clock: FakeClock | None = None
    # probe calls that fail: method name -> error, or (method, pid) -> error
    probe_failures: dict[str | tuple[str, int], Exception] = field(default_factory=dict)
    # recordings
    budgets: list[float] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    alive_at_start: list[int] = field(default_factory=list)
    signalled: list[int] = field(default_factory=list)
    tokens_to: list[frozenset[int]] = field(default_factory=list)

    def spawn(
        self,
        pid: int,
        *,
        on_port: bool = True,
        version: str = WHEEL,
        survives: bool = False,
        slow_exit: bool = False,
        refuses: bool = False,
    ) -> FakeProcess:
        proc = FakeProcess(
            self,
            pid,
            version=version,
            survives=survives,
            slow_exit=slow_exit,
            refuses=refuses,
        )
        if on_port:
            proc.bind()
        self.procs[pid] = proc
        return proc

    def run_unit(
        self, pid: int, *, on_port: bool = True, version: str = WHEEL
    ) -> FakeProcess:
        """The manager runs *pid* as its main process."""
        proc = self.spawn(pid, on_port=on_port, version=version)
        self.unit_active = True
        self.mainpid = pid
        return proc

    def unit_stops(self) -> None:
        """The manager stops its job: the main process exits (systemctl stop)."""
        if self.mainpid is not None:
            self.procs[self.mainpid].exit()
        self.unit_active = False
        self.mainpid = None

    def unit_starts(self) -> None:
        """The manager starts a fresh main process that binds and serves."""
        pid = self.next_pid
        self.next_pid += 1
        self.run_unit(pid, on_port=self.start_binds, version=self.start_version)

    def holder(self) -> FakeProcess | None:
        held = [p for p in self.procs.values() if p.on_port]
        return held[0] if held else None


@final
class FakeBackend:
    """A ``ServiceBackend`` over the shared world."""

    __slots__ = ("_world",)

    _world: FakeWorld

    def __new__(cls, world: FakeWorld) -> Self:
        self = super().__new__(cls)
        self._world = world
        return self

    def is_installed(self) -> bool:
        world = self._world
        world.calls.append("is_installed")
        if world.calls.count("is_installed") == 2 and world.between is not None:
            world.between()
        return world.installed

    def is_active(self) -> bool:
        self._world.calls.append("is_active")
        self._raise_query_error()
        return self._world.unit_active

    def main_pid(self) -> int | None:
        self._world.calls.append("main_pid")
        self._raise_query_error()
        return self._world.mainpid

    def _raise_query_error(self) -> None:
        if self._world.query_error is not None:
            raise self._world.query_error

    def installed_bind(self) -> str:
        return ""

    def request_stop(self) -> None:
        world = self._world
        world.calls.append("request_stop")
        if world.on_stop is not None:
            world.on_stop()
        elif world.stop_takes_effect:
            world.unit_stops()
        if world.stop_error is not None:
            raise world.stop_error

    def request_start(self) -> None:
        world = self._world
        world.calls.append("request_start")
        world.alive_at_start.extend(p.pid for p in world.procs.values() if p.alive)
        if world.on_start is not None:
            world.on_start()
        if world.start_takes_effect:
            world.unit_starts()
        if world.start_error is not None:
            raise world.start_error


@final
class FakeProbe:
    """A ``LifecycleProbe`` over the shared world."""

    __slots__ = ("_miss", "_world")

    _world: FakeWorld
    _miss: str

    def __new__(cls, world: FakeWorld) -> Self:
        self = super().__new__(cls)
        self._world = world
        self._miss = ""
        return self

    def _check(self) -> None:
        if self._world.probe_error:
            raise PortProbeError(self._world.probe_error)

    def voxd_processes(self) -> frozenset[FakeHandle]:
        self._check()
        hook, self._world.before_holders = self._world.before_holders, None
        if hook is not None:
            hook()
        return frozenset(FakeHandle(p) for p in self._world.procs.values() if p.on_port)

    def off_port_voxd(self) -> frozenset[int]:
        self._check()
        return self._world.off_port

    def open_handle(self, pid: int) -> FakeHandle | None:
        proc = self._world.procs.get(pid)
        return FakeHandle(proc) if proc is not None and proc.alive else None

    def _fail(self, *keys: str | tuple[str, int]) -> None:
        for key in keys:
            failure = self._world.probe_failures.get(key)
            if failure is not None:
                raise failure

    def service_owned(self, pid: int) -> bool:
        self._fail("service_owned", ("service_owned", pid))
        return pid in self._world.service_pids

    def clear_port_file(self) -> PortFileCleanup:
        world = self._world
        world.calls.append("clear_port_file")
        hook, world.before_clear = world.before_clear, None
        if hook is not None:
            hook()
        self._fail("clear_port_file")
        if world.holder() is not None:
            return PortFileCleanup.HELD
        if world.port_file_stale or world.port_file == 8421:
            world.port_file = None
            world.port_file_stale = False
            return PortFileCleanup.REMOVED
        if world.port_file is None:
            return PortFileCleanup.ABSENT
        return PortFileCleanup.OTHER_PORT

    def port_listening(self) -> bool:
        self._fail("port_listening")
        return self._world.foreign_listener or self._world.holder() is not None

    def health(self, holders: frozenset[ProcessHandle]) -> HealthStatus | None:
        if not holders:
            self._miss = "no visible voxd process holds port 8421"
            return None
        self._world.tokens_to.append(frozenset(h.pid for h in holders))
        holder = self._world.holder()
        if holder is None or not self._world.token_written:
            self._miss = self._world.health_error
            return None
        return HealthStatus(
            status="ok", port=8421, pid=holder.pid, daemon_version=holder.version
        )

    def health_miss(self) -> Exception | None:
        return RuntimeError(self._miss) if self._miss else None


@final
class FakeClock:
    """A monotonic clock that only moves when slept; sleeping fires events."""

    __slots__ = ("_events", "_now")

    _now: float
    _events: list[tuple[float, Callable[[], None]]]

    def __new__(cls) -> Self:
        self = super().__new__(cls)
        self._now = 0.0
        self._events = []
        return self

    def at(self, when: float, event: Callable[[], None]) -> None:
        """Run *event* once the clock reaches *when*."""
        self._events.append((when, event))

    def monotonic(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        """Move time forward without firing events, as a blocking call does."""
        self._now += seconds

    def sleep(self, seconds: float) -> None:
        self._now += seconds
        due = [e for e in self._events if e[0] <= self._now]
        self._events = [e for e in self._events if e[0] > self._now]
        for _, event in due:
            event()


@final
class FakeSudo:
    """A ``CommandRunner`` with scripted exit codes per argv; records calls."""

    __slots__ = ("_script", "calls")

    _script: dict[tuple[str, ...], list[tuple[int, str]]]
    calls: list[tuple[tuple[str, ...], bool]]

    def __new__(cls, script: dict[tuple[str, ...], list[tuple[int, str]]]) -> Self:
        self = super().__new__(cls)
        self._script = {k: list(v) for k, v in script.items()}
        self.calls = []
        return self

    def __call__(self, argv: Sequence[str], *, capture: bool) -> tuple[int, str]:
        key = tuple(argv)
        self.calls.append((key, capture))
        answers = self._script.get(key)
        if not answers:
            msg = f"unscripted command: {argv}"
            raise AssertionError(msg)
        return answers.pop(0) if len(answers) > 1 else answers[0]


# -- the modeled properties, shared by the per-verb row tests --------------

_ALLOWED: dict[str, frozenset[LifecycleOutcome]] = {
    "stop": frozenset(
        {
            LifecycleOutcome.STOPPED,
            LifecycleOutcome.ALREADY_STOPPED,
            LifecycleOutcome.NOT_INSTALLED,
            LifecycleOutcome.PERMISSION_DENIED,
            LifecycleOutcome.BACKEND_FAILED,
            LifecycleOutcome.TIMEOUT,
        }
    ),
    "start": frozenset(
        {
            LifecycleOutcome.STARTED,
            LifecycleOutcome.ALREADY_RUNNING,
            LifecycleOutcome.NOT_INSTALLED,
            LifecycleOutcome.PERMISSION_DENIED,
            LifecycleOutcome.BACKEND_FAILED,
            LifecycleOutcome.TIMEOUT,
            LifecycleOutcome.PORT_CONFLICT,
            LifecycleOutcome.VERSION_MISMATCH,
        }
    ),
    "restart": frozenset(
        {
            LifecycleOutcome.RESTARTED,
            LifecycleOutcome.NOT_INSTALLED,
            LifecycleOutcome.PERMISSION_DENIED,
            LifecycleOutcome.BACKEND_FAILED,
            LifecycleOutcome.TIMEOUT,
            LifecycleOutcome.PORT_CONFLICT,
            LifecycleOutcome.VERSION_MISMATCH,
        }
    ),
}


def denied() -> PrivilegeDeniedError:
    return PrivilegeDeniedError("sudo: a password is required")


def failed(verb: str) -> BackendCommandError:
    return BackendCommandError.of(["systemctl", verb, "voxd"], 1, "Job failed")


def assert_p1_one_outcome(report: LifecycleReport) -> None:
    """P1 (and the section 4.4 table): the outcome is in the verb's closed set."""
    assert report.outcome in _ALLOWED[report.verb]
    assert report.exit_code == (0 if report.outcome.ok else 1)


def assert_p2_start_after_exit(world: FakeWorld) -> None:
    """P2: when start acted, no previous or main process was alive."""
    assert world.alive_at_start == []


def assert_p4_token_only_to_visible_voxd(world: FakeWorld, *allowed: int) -> None:
    """P4: every token went to a visible voxd holding 8421, and only to it."""
    for holders in world.tokens_to:
        assert holders, "a token went out with no visible voxd on the port"
        assert holders <= set(allowed)


def assert_p5_no_signal(world: FakeWorld, *forbidden: int) -> None:
    """P5: no signal reached the manager's main process or an off-port voxd."""
    assert not set(world.signalled) & set(forbidden)


def lifecycle(world: FakeWorld, clock: FakeClock | None = None) -> DaemonLifecycle:
    """Return a lifecycle over *world*, polling on a fake clock."""
    world.clock = clock if clock is not None else FakeClock()
    poll = Poll(world.clock)
    system = LifecycleSystem(FakeBackend(world), FakeProbe(world), poll, WHEEL)
    return DaemonLifecycle(system)


__all__ = [
    "STALE",
    "WHEEL",
    "BackendCommandError",
    "FakeBackend",
    "FakeClock",
    "FakeHandle",
    "FakeProbe",
    "FakeProcess",
    "FakeSudo",
    "FakeWorld",
    "PrivilegeDeniedError",
    "assert_p1_one_outcome",
    "assert_p2_start_after_exit",
    "assert_p4_token_only_to_visible_voxd",
    "assert_p5_no_signal",
    "denied",
    "failed",
    "lifecycle",
]
