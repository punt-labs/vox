"""Why a health request never went out: the token stayed home.

Each gate in front of a token-bearing health request names its own reason
here. A :class:`NotAskedError` is recorded as the probe's ``health_miss``
only; what it says about voxd is "unknown", never "unresponsive", because
voxd was not asked.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Self

from punt_vox.service.process import DEFAULT_PORT

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = ["NotAskedError"]


class NotAskedError(RuntimeError):
    """voxd was never asked: no token-bearing health request went out.

    The base of one small hierarchy: :class:`~punt_vox.service.health.
    DaemonNotUpError` (voxd's token not available) is the one subclass, so
    a missing token reads as "not asked" wherever a reading classifies it.
    """

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
    def lookup_spent(cls) -> Self:
        """Return the miss for a read of the installed bind that spent the budget."""
        return cls(
            "reading the installed bind spent the health budget before voxd "
            "could be asked; no token sent unattributed"
        )

    @classmethod
    def unpublished(cls) -> Self:
        """Return the miss for no ``serve.port`` at all."""
        return cls("serve.port publishes no port; no token sent unattributed")

    @classmethod
    def unparseable(cls) -> Self:
        """Return the miss for a ``serve.port`` that does not name a port."""
        return cls("serve.port does not name a port; no token sent unattributed")

    @classmethod
    def other_port(cls, port: int) -> Self:
        """Return the miss for a ``serve.port`` naming a port not the service's."""
        return cls(
            f"serve.port names port {port}, not the service port {DEFAULT_PORT}; "
            "no token sent unattributed"
        )

    @classmethod
    def not_owned(cls, name: str, uid: int) -> Self:
        """Return the miss for a run file *name* another user owns."""
        return cls(
            f"{name} belongs to uid {uid}, not this user; no token sent unattributed"
        )

    @classmethod
    def uncheckable(cls, why: str) -> Self:
        """Return the miss for a check the gate needed and could not complete.

        A run file vox could not read or stat, or a ``ps`` it could not run:
        *why* carries the failure's own text.
        """
        return cls(f"{why}; no token sent unattributed")

    @classmethod
    def republished(cls) -> Self:
        """Return the miss for a ``serve.port`` replaced while the pair was read."""
        return cls(
            "serve.port was replaced while it was being read; "
            "no token sent unattributed"
        )

    @classmethod
    def no_pid(cls) -> Self:
        """Return the miss for no ``serve.pid`` beside ``serve.port``."""
        return cls("serve.pid publishes no pid; no token sent unattributed")

    @classmethod
    def pid_unparseable(cls) -> Self:
        """Return the miss for a ``serve.pid`` that does not name a pid."""
        return cls("serve.pid does not name a pid; no token sent unattributed")

    @classmethod
    def publisher_gone(cls, pid: int) -> Self:
        """Return the miss for a published pid that is no longer alive."""
        return cls(
            f"voxd pid {pid} that published serve.port is gone; "
            "no token sent unattributed"
        )

    @classmethod
    def publisher_foreign(cls, pid: int) -> Self:
        """Return the miss for a published pid that is another user's process."""
        return cls(
            f"pid {pid} named in serve.pid is another user's process; "
            "no token sent unattributed"
        )

    @classmethod
    def not_listening(cls, pid: int) -> Self:
        """Return the miss for a voxd on the port that holds no listener there."""
        return cls(
            f"voxd pid {pid} has a socket on port {DEFAULT_PORT} but holds no "
            "listener there; no token sent"
        )

    @classmethod
    def publisher_without_port(cls, pid: int) -> Self:
        """Return the miss for a live publisher that no longer holds the port."""
        return cls(
            f"voxd pid {pid} that published serve.port no longer holds port "
            f"{DEFAULT_PORT}; no token sent unattributed"
        )

    @classmethod
    def holder_uncheckable(cls, why: str) -> Self:
        """Return the miss for a holder check that could not be made in time."""
        return cls(
            f"cannot tell whether the publisher holds port {DEFAULT_PORT}: {why}; "
            "no token sent unattributed"
        )

    @classmethod
    def not_voxd(cls, pid: int, command: str) -> Self:
        """Return the miss for a published pid whose command is not voxd."""
        return cls(
            f"pid {pid} named in serve.pid is not voxd (command: {command}); "
            "no token sent unattributed"
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
