"""Ask voxd for health through the port it published, before any attribution.

``vox daemon status`` cannot wait for ``lsof`` on a loaded Mac, where it walks
every open file. This request goes out first, under a gate made of
what voxd itself publishes and what vox can check without ``lsof``: this
user's own ``serve.port`` names the service port, the target host is
loopback, and the port accepts a plain connection. A gate that does not open
sends nothing and records why; a check the gate cannot complete -- a
``serve.port`` it cannot read, a connect it cannot finish -- closes this door
and leaves attribution to decide, never the whole reading. A request that
fails records its cause. The token goes out through no other unattributed
door.
"""

from __future__ import annotations

import ipaddress
from typing import TYPE_CHECKING, Self, final

from punt_vox.service.backend import PortProbeError
from punt_vox.service.health_request import HealthRequest
from punt_vox.service.not_asked import NotAskedError
from punt_vox.service.process import DEFAULT_PORT
from punt_vox.service.published_port import PublishedPort

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from punt_vox.service.deadline import Deadline
    from punt_vox.service.health import HealthTarget
    from punt_vox.types_health import HealthStatus

__all__ = ["PublishedHealth"]


@final
class PublishedHealth:
    """One gated, unattributed health request to the published port."""

    __slots__ = ("_bound", "_listening", "_miss", "_port_file", "_target")

    _port_file: Path
    _target: Callable[[], HealthTarget]
    _listening: Callable[[HealthTarget, Deadline], bool]
    _bound: Deadline
    _miss: Exception | None

    def __new__(
        cls,
        port_file: Path,
        target: Callable[[], HealthTarget],
        listening: Callable[[HealthTarget, Deadline], bool],
        bound: Deadline,
    ) -> Self:
        """Create the request: *target* is resolved, and *listening* checked,
        inside *bound*."""
        self = super().__new__(cls)
        self._port_file = port_file
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
        unasked = self._file_gate()
        if unasked is not None:
            self._miss = unasked
            return None
        # Resolved inside the bound: a slow read of the installed bind spends
        # from the share, so the connect and the request cannot run past it.
        target = self._target()
        unasked = self._target_gate(target)
        if unasked is not None:
            self._miss = unasked
            return None
        request = HealthRequest(target, self._bound)
        health = request.send()
        self._miss = request.miss
        return health

    def _file_gate(self) -> NotAskedError | None:
        """Return why ``serve.port`` closes the gate; ``None`` when it opens."""
        try:
            return PublishedPort.at(self._port_file).unasked(DEFAULT_PORT)
        except PortProbeError as exc:
            # A serve.port vox cannot read closes this door only: attribution
            # still runs, and a voxd lsof finds is still asked.
            return NotAskedError.unreadable(str(exc))

    def _target_gate(self, target: HealthTarget) -> NotAskedError | None:
        """Return why *target* may not be asked; ``None`` to ask."""
        if self._bound.remaining() < HealthRequest.MIN_S:
            return NotAskedError.lookup_spent()
        if not ipaddress.ip_address(target.host).is_loopback:
            return NotAskedError.not_loopback(target.host)
        return self._listener_gate(target)

    def _listener_gate(self, target: HealthTarget) -> NotAskedError | None:
        """Return why the listener check closed the gate; ``None`` to ask."""
        try:
            listening = self._listening(target, self._bound)
        except PortProbeError as exc:
            # A connect vox cannot complete is no reason to abandon the
            # reading: attribution still runs, and may still ask.
            return NotAskedError.cannot_connect(str(exc))
        if not listening:
            return NotAskedError.nothing_listens()
        if self._bound.remaining() >= HealthRequest.MIN_S:
            return None
        return NotAskedError.budget_spent()
