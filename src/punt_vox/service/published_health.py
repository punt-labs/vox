"""Ask voxd for health through the port it published, before any attribution.

``vox daemon status`` cannot wait for ``lsof`` on a loaded Mac, where it walks
every open file. This request goes out first, under a gate made of what voxd
itself publishes and what vox can check without ``lsof``: this user's own
``serve.port`` names the service port and ``serve.pid`` a pid that is alive
and this user's (:class:`PublishedPort`), the target host is loopback, ``ps``
says that pid runs voxd, that pid itself holds the listener on the port, and
the port accepts a plain connection. A port file a crash left behind, or a
voxd whose listening socket is already closed while its process lingers in
shutdown, therefore cannot send the token to another user who binds the
port next. A gate that does not open sends nothing and records why;
a check the gate cannot complete -- a run file it cannot read, a ``ps`` or a
connect it cannot finish -- closes this door and leaves attribution to
decide, never the whole reading. A request that fails records its cause. The
token goes out through no other unattributed door.
"""

from __future__ import annotations

import ipaddress
from typing import TYPE_CHECKING, Self, final

from punt_vox.service.backend import PortProbeError
from punt_vox.service.health_request import HealthRequest
from punt_vox.service.not_asked import NotAskedError
from punt_vox.service.process import DEFAULT_PORT, ProcessManager
from punt_vox.service.published_port import PublishedPort

if TYPE_CHECKING:
    from collections.abc import Callable

    from punt_vox.service.deadline import Deadline
    from punt_vox.service.health import HealthTarget
    from punt_vox.types_health import HealthStatus

__all__ = ["PublishedHealth"]


@final
class PublishedHealth:
    """One gated, unattributed health request to the published port."""

    __slots__ = ("_bound", "_listening", "_miss", "_processes", "_target")

    _processes: ProcessManager
    _target: Callable[[], HealthTarget]
    _listening: Callable[[HealthTarget, Deadline], bool]
    _bound: Deadline
    _miss: Exception | None

    def __new__(
        cls,
        processes: ProcessManager,
        target: Callable[[], HealthTarget],
        listening: Callable[[HealthTarget, Deadline], bool],
        bound: Deadline,
    ) -> Self:
        """Create the request over *processes*' run files and ``ps``.

        *target* is resolved, and *listening* checked, inside *bound*; the
        ``ps`` that names the publisher's command is cut to it as well.
        """
        self = super().__new__(cls)
        self._processes = processes
        self._target = target
        self._listening = listening
        self._bound = bound
        self._miss = None
        return self

    @property
    def miss(self) -> Exception | None:
        """Return why no answer came; ``None`` after an answer."""
        return self._miss

    def ask(self) -> HealthStatus | None:
        """Return voxd's health, or ``None`` with :attr:`miss` set."""
        try:
            target = self._gate()
        except NotAskedError as closed:
            self._miss = closed
            return None
        request = HealthRequest(target, self._bound)
        health = request.send()
        self._miss = request.miss
        return health

    def _gate(self) -> HealthTarget:
        """Return the target to ask; raise ``NotAskedError`` naming a closed gate.

        Raises:
            NotAskedError: If any check closes the gate, or cannot complete.
        """
        pid = self._publisher()
        # Resolved inside the bound: a slow read of the installed bind spends
        # from the share, so the connect and the request cannot run past it.
        target = self._target()
        if self._bound.remaining() < HealthRequest.MIN_S:
            raise NotAskedError.lookup_spent()
        if not ipaddress.ip_address(target.host).is_loopback:
            raise NotAskedError.not_loopback(target.host)
        self._require_voxd(pid)
        self._require_holder(pid)
        self._require_listener(target)
        return target

    def _publisher(self) -> int:
        """Return the live pid the run files name; raise why there is none.

        A run file vox cannot read closes this door only: attribution still
        runs, and a voxd ``lsof`` finds is still asked.

        Raises:
            NotAskedError: If the run files do not name this user's live voxd.
        """
        try:
            return PublishedPort.at(self._processes.port_file).publisher(DEFAULT_PORT)
        except PortProbeError as exc:
            raise NotAskedError.uncheckable(str(exc)) from exc

    def _require_voxd(self, pid: int) -> None:
        """Raise unless ``ps``, cut to the bound, says *pid* runs voxd.

        Raises:
            NotAskedError: If *pid* is gone, is not voxd, or ``ps`` could not run.
        """
        try:
            seen = self._processes.within(self._bound).identity(pid)
        except PortProbeError as exc:
            raise NotAskedError.uncheckable(str(exc)) from exc
        if seen is None:
            raise NotAskedError.publisher_gone(pid)
        if not seen.is_voxd():
            raise NotAskedError.not_voxd(pid, seen.command)

    def _require_holder(self, pid: int) -> None:
        """Raise unless *pid* itself holds the listener, checked within the bound.

        Raises:
            NotAskedError: If *pid* does not hold the port, or the check could
                not be made in time.
        """
        try:
            holds = self._processes.within(self._bound).holds_port(pid, DEFAULT_PORT)
        except PortProbeError as exc:
            raise NotAskedError.holder_uncheckable(str(exc)) from exc
        if not holds:
            raise NotAskedError.publisher_without_port(pid)

    def _require_listener(self, target: HealthTarget) -> None:
        """Raise unless something accepts a connection, with the floor still left.

        Raises:
            NotAskedError: If nothing listens, the connect could not complete,
                or less than a request's floor is left after it.
        """
        try:
            listening = self._listening(target, self._bound)
        except PortProbeError as exc:
            # A connect vox cannot complete is no reason to abandon the
            # reading: attribution still runs, and may still ask.
            raise NotAskedError.cannot_connect(str(exc)) from exc
        if not listening:
            raise NotAskedError.nothing_listens()
        if self._bound.remaining() < HealthRequest.MIN_S:
            raise NotAskedError.budget_spent()
