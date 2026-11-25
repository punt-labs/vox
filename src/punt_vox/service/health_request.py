"""One token-bearing health request to voxd, bounded, with its failure kept.

Both doors that send the token -- the attributed request in
:mod:`punt_vox.service.daemon_probe` and the published-port request in
:mod:`punt_vox.service.published_health` -- send it through this class, so
the request is bounded and its failures are classified in exactly one place.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Self, final

from punt_vox.client_errors import VoxdConnectionError, VoxdProtocolError
from punt_vox.service.health import DaemonNotUpError

if TYPE_CHECKING:
    from punt_vox.service.deadline import Deadline
    from punt_vox.service.health import HealthTarget
    from punt_vox.types_health import HealthStatus

__all__ = ["HealthRequest"]


@final
class HealthRequest:
    """The request *target* answers within what is left of *bound*."""

    __slots__ = ("_bound", "_miss", "_target")

    # The least a request is given. With less left, a timeout would measure
    # vox's own probing, not voxd, so the gates send nothing below it.
    MIN_S: ClassVar[float] = 0.5

    _target: HealthTarget
    _bound: Deadline
    _miss: Exception | None

    def __new__(cls, target: HealthTarget, bound: Deadline) -> Self:
        """Create a request to *target* cut off at *bound*."""
        self = super().__new__(cls)
        self._target = target
        self._bound = bound
        self._miss = None
        return self

    @property
    def miss(self) -> Exception | None:
        """Return why the request got no answer; ``None`` before or after one."""
        return self._miss

    def send(self) -> HealthStatus | None:
        """Send the request; ``None`` is the documented answer for no reply.

        The token comes from ``serve.token`` through the target's client,
        never from the shell; with no token file nothing is sent.
        """
        try:
            return self._target.client().health(timeout=self._bound.remaining())
        except (
            DaemonNotUpError,
            VoxdConnectionError,
            VoxdProtocolError,
            OSError,
        ) as exc:
            self._miss = exc
            return None
