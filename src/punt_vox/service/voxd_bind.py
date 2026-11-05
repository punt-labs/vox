"""The address voxd binds, validated once for install and for health.

``VOXD_BIND`` must be an IP address, never a name. A name could resolve to an
address voxd never bound (``localhost`` to ``::1`` while voxd holds
``127.0.0.1``), where another local user would receive the token the health
request carries, and resolving it is bounded by no deadline. Install refuses a
name before it writes anything; health refuses one read back from a unit that
predates the check. Both ask this class, so they cannot disagree.
"""

from __future__ import annotations

import ipaddress
from typing import TYPE_CHECKING, ClassVar, Self, final

from punt_vox.service.backend import PortProbeError

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["VoxdBind"]


@final
class VoxdBind:
    """An IP address voxd binds; empty means its loopback default."""

    __slots__ = ("_address",)

    # A wildcard accepts loopback of its own family only: an IPv6 socket bound
    # to ``::`` with IPV6_V6ONLY (the BSD default) refuses 127.0.0.1.
    _LOOPBACK: ClassVar[dict[int, str]] = {4: "127.0.0.1", 6: "::1"}

    _address: ipaddress.IPv4Address | ipaddress.IPv6Address

    def __new__(cls, raw: str) -> Self:
        self = super().__new__(cls)
        bind = raw.strip() or "127.0.0.1"
        try:
            self._address = ipaddress.ip_address(bind)
        except ValueError:
            msg = (
                f"VOXD_BIND {bind!r} is not an IP address; vox polls only the "
                "exact address voxd binds. Set VOXD_BIND to an IP "
                "(e.g. 127.0.0.1) and run `vox daemon install`"
            )
            raise PortProbeError(msg) from None
        return self

    @classmethod
    def from_environ(cls, environ: Mapping[str, str]) -> Self:
        """Return the bind the installed unit will carry from *environ*."""
        return cls(environ.get("VOXD_BIND", ""))

    def poll_host(self) -> str:
        """Return the address a health request reaches voxd on.

        A wildcard (unspecified) bind accepts its family's loopback, so poll
        ``127.0.0.1`` for ``0.0.0.0`` and ``::1`` for ``::``; a concrete IP is
        the only address voxd listens on, so poll it exactly.
        """
        if self._address.is_unspecified:
            return self._LOOPBACK[self._address.version]
        return str(self._address)
