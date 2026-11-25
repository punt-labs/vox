"""Why a health request never went out: the token stayed home.

Each gate in front of a token-bearing health request names its own reason
here. A :class:`NotAskedError` is recorded as the probe's ``health_miss``
only; what it says about voxd is "unknown", never "unresponsive", because
voxd was not asked.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, final

from punt_vox.service.process import DEFAULT_PORT

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = ["NotAskedError"]


@final
class NotAskedError(RuntimeError):
    """voxd was never asked: no token-bearing health request went out."""

    __slots__ = ()

    @classmethod
    def left_port(cls, pids: Iterable[int]) -> Self:
        """Return the miss for attributed voxd *pids* that left the port."""
        named = ", ".join(str(pid) for pid in sorted(pids))
        return cls(
            f"voxd pid {named} left port {DEFAULT_PORT} before it could be "
            "asked; no token sent"
        )

    @classmethod
    def budget_spent(cls) -> Self:
        """Return the miss for a re-check that left less than a request's floor."""
        return cls(
            f"re-checking port {DEFAULT_PORT} spent the health budget before "
            "voxd could be asked; no token sent"
        )

    @classmethod
    def unpublished(cls, port: int | None) -> Self:
        """Return the miss for a ``serve.port`` that does not name our service."""
        if port is None:
            return cls("serve.port publishes no port; no token sent unattributed")
        return cls(
            f"serve.port names port {port}, not this user's voxd on "
            f"{DEFAULT_PORT}; no token sent unattributed"
        )

    @classmethod
    def not_loopback(cls, host: str) -> Self:
        """Return the miss for a bind that is not loopback."""
        return cls(f"voxd binds {host}, not loopback; no token sent unattributed")

    @classmethod
    def cannot_connect(cls, why: str) -> Self:
        """Return the miss for a listener check that could not complete."""
        return cls(f"{why}; no token sent unattributed")

    @classmethod
    def nothing_listens(cls) -> Self:
        """Return the miss for a published port that refuses a connection."""
        return cls(f"nothing listens on port {DEFAULT_PORT}; no token sent")
