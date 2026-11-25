"""Ask a voxd that ``lsof``/``fuser`` attributed to the port for health.

The attributed door for the token: the holders were seen on the service port
as voxd processes, and are attributed again immediately before the token
goes out, so a process that took the port between attribution and the
request is never sent it. With no visible holder nothing is sent. The
re-check spends from the request's own bound, and the request gets what is
left.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, final

from punt_vox.service.health import DaemonNotUpError
from punt_vox.service.health_request import HealthRequest
from punt_vox.service.not_asked import NotAskedError
from punt_vox.service.process import DEFAULT_PORT

if TYPE_CHECKING:
    from collections.abc import Callable

    from punt_vox.service.backend import ProcessHandle
    from punt_vox.service.deadline import Deadline
    from punt_vox.service.health import HealthTarget
    from punt_vox.types_health import HealthStatus

__all__ = ["AttributedHealth"]


@final
class AttributedHealth:
    """One gated health request to the voxd *holders* attributed to the port."""

    __slots__ = ("_bound", "_holders", "_miss", "_rescan", "_target")

    _holders: frozenset[ProcessHandle]
    _rescan: Callable[[], frozenset[ProcessHandle]]
    _target: Callable[[], HealthTarget]
    _bound: Deadline
    _miss: Exception | None

    def __new__(
        cls,
        holders: frozenset[ProcessHandle],
        rescan: Callable[[], frozenset[ProcessHandle]],
        target: Callable[[], HealthTarget],
        bound: Deadline,
    ) -> Self:
        """Create the request: *rescan* re-attributes the port before the send."""
        self = super().__new__(cls)
        self._holders = holders
        self._rescan = rescan
        self._target = target
        self._bound = bound
        self._miss = None
        return self

    @property
    def miss(self) -> Exception | None:
        """Return why no answer came; ``None`` after an answer."""
        return self._miss

    def ask(self) -> HealthStatus | None:
        """Return voxd's health, or ``None`` with :attr:`miss` set."""
        if not self._holders:
            self._miss = DaemonNotUpError(
                f"no visible voxd process holds port {DEFAULT_PORT}"
            )
            return None
        # The bind is resolved before the re-check so an unreadable unit is
        # reported as such, not as a spent budget.
        target = self._target()
        unasked = self._unasked()
        if unasked is not None:
            self._miss = unasked
            return None
        request = HealthRequest(target, self._bound)
        health = request.send()
        self._miss = request.miss
        return health

    def _unasked(self) -> NotAskedError | None:
        """Attribute the port again, immediately before the token goes out.

        Returns why no request may be sent -- a holder seen at attribution no
        longer holds the port, as the same pinned process, or the re-check
        left less than a request's floor -- or ``None`` when voxd may be asked.
        """
        if left := self._holders - self._rescan():
            return NotAskedError.left_port(h.pid for h in left)
        if self._bound.remaining() >= HealthRequest.MIN_S:
            return None
        return NotAskedError.budget_spent()
