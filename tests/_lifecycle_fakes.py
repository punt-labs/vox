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
    BackendCommandError,
    PortProbeError,
    PrivilegeDeniedError,
)
from punt_vox.service.clock import Poll
from punt_vox.service.lifecycle import DaemonLifecycle
from punt_vox.service.lifecycle_system import LifecycleSystem
from punt_vox.types_health import HealthStatus

if TYPE_CHECKING:
    from punt_vox.service.backend import ProcessHandle

WHEEL = "5.0.6"
STALE = "5.0.5"


@final
class FakeProcess:
    """One voxd process: alive or not, on the port or not, killable or not."""

    __slots__ = ("_on_port", "_pid", "_survives", "_world", "alive", "version")

    _world: FakeWorld
    _pid: int
    _on_port: bool
    _survives: bool
    alive: bool
    version: str

    def __new__(
        cls, world: FakeWorld, pid: int, *, version: str = WHEEL, survives: bool = False
    ) -> Self:
        self = super().__new__(cls)
        self._world = world
        self._pid = pid
        self._on_port = False
        self._survives = survives
        self.alive = True
        self.version = version
        return self

    @property
    def pid(self) -> int:
        return self._pid

    @property
    def on_port(self) -> bool:
        return self.alive and self._on_port

    def bind(self) -> None:
        self._on_port = True

    def unbind(self) -> None:
        self._on_port = False

    def exit(self) -> None:
        self.alive = False
        self._on_port = False

    def exited(self) -> bool:
        return not self.alive

    def terminate(self) -> bool:
        self._world.signalled.append(self._pid)
        if not self._survives:
            self.exit()
        return not self.alive


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
    # an actor other than vox: replaces the manager's stop, or acts between
    # the stop and start halves of restart (at the second observation)
    on_stop: Callable[[], None] | None = None
    between: Callable[[], None] | None = None
    # recordings
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
    ) -> FakeProcess:
        proc = FakeProcess(self, pid, version=version, survives=survives)
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
        return self._world.unit_active

    def main_pid(self) -> int | None:
        self._world.calls.append("main_pid")
        return self._world.mainpid

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
        if world.start_takes_effect:
            world.unit_starts()
        if world.start_error is not None:
            raise world.start_error


@final
class FakeProbe:
    """A ``LifecycleProbe`` over the shared world."""

    __slots__ = ("_world",)

    _world: FakeWorld

    def __new__(cls, world: FakeWorld) -> Self:
        self = super().__new__(cls)
        self._world = world
        return self

    def _check(self) -> None:
        if self._world.probe_error:
            raise PortProbeError(self._world.probe_error)

    def voxd_processes(self) -> frozenset[FakeProcess]:
        self._check()
        return frozenset(p for p in self._world.procs.values() if p.on_port)

    def off_port_voxd(self) -> frozenset[int]:
        self._check()
        return self._world.off_port

    def open_handle(self, pid: int) -> FakeProcess | None:
        proc = self._world.procs.get(pid)
        return proc if proc is not None and proc.alive else None

    def port_listening(self) -> bool:
        return self._world.foreign_listener or self._world.holder() is not None

    def health(self, holders: frozenset[ProcessHandle]) -> HealthStatus | None:
        if not holders:
            return None
        self._world.tokens_to.append(frozenset(h.pid for h in holders))
        holder = self._world.holder()
        if holder is None or not self._world.token_written:
            return None
        return HealthStatus(
            status="ok", port=8421, pid=holder.pid, daemon_version=holder.version
        )


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


def lifecycle(world: FakeWorld, clock: FakeClock | None = None) -> DaemonLifecycle:
    """Return a lifecycle over *world*, polling on a fake clock."""
    poll = Poll(clock if clock is not None else FakeClock())
    system = LifecycleSystem(FakeBackend(world), FakeProbe(world), poll, WHEEL)
    return DaemonLifecycle(system)


__all__ = [
    "STALE",
    "WHEEL",
    "BackendCommandError",
    "FakeBackend",
    "FakeClock",
    "FakeProbe",
    "FakeProcess",
    "FakeSudo",
    "FakeWorld",
    "PrivilegeDeniedError",
    "lifecycle",
]
