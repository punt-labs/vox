"""The local addresses a listener must carry to serve the host a request reaches.

A health request goes to the unit's polled host, so a LISTEN socket on the
port at another address is not the one it reaches: voxd on 127.0.0.1 and a
foreign process on [::1] are different listeners. The exact address counts,
and so does the wildcard of the host's own family, which serves it -- but
never the other family's: uvicorn sets ``IPV6_V6ONLY``, so ``[::]`` does not
serve 127.0.0.1, and ``0.0.0.0`` never serves ``::1``. An IPv4 host is also
served by its v4-mapped form on an IPv6 socket.

Pure computation, no I/O: the probe's holder check matches these forms
against ``lsof -Fnt`` records on macOS and ``/proc/net/tcp`` rows on Linux.
"""

from __future__ import annotations

import ipaddress
import socket
import struct
from typing import Self, final

__all__ = ["ListenAddress"]


@final
class ListenAddress:
    """The local addresses a listener on *port* may have to serve *host*.

    A health request goes to the unit's polled host, so a LISTEN socket on the
    port at another address is not the one it reaches: voxd on 127.0.0.1 and a
    foreign process on [::1] are different listeners. The exact address, the
    family's wildcard (which serves it), and, for an IPv4 host, the v4-mapped
    form a dual-stack socket shows, are the forms that count.
    """

    __slots__ = ("_host", "_port")

    _host: ipaddress.IPv4Address | ipaddress.IPv6Address
    _port: int

    def __new__(cls, host: str, port: int) -> Self:
        """Create the address for *host* (an IP literal) on *port*."""
        self = super().__new__(cls)
        self._host = ipaddress.ip_address(host)
        self._port = port
        return self

    @property
    def port(self) -> int:
        """Return the port."""
        return self._port

    def lsof_matches(self, family: str, name: str) -> bool:
        """Return True when an ``lsof -Fnt`` socket (*family*, *name*) serves the host.

        *family* is lsof's type field, ``IPv4`` or ``IPv6``. A wildcard
        listener counts only in the host's own family: uvicorn sets
        ``IPV6_V6ONLY``, so ``[::]`` does not serve 127.0.0.1, and ``0.0.0.0``
        never serves ``::1``. An IPv4 host is also served by its v4-mapped
        form on an IPv6 socket.
        """
        port = self._port
        if self._host.version == 6:
            return name == f"[{self._host}]:{port}" or (
                family == "IPv6" and name == f"*:{port}"
            )
        return name in {f"{self._host}:{port}", f"[::ffff:{self._host}]:{port}"} or (
            family == "IPv4" and name == f"*:{port}"
        )

    def proc_forms(self, table: str) -> frozenset[str]:
        """Return the ``local_address`` hex forms that match in ``/proc/net/<table>``.

        A wildcard counts only in the host's own family (``IPV6_V6ONLY``:
        ``[::]`` does not serve an IPv4 host); an IPv4 host is also served by
        its v4-mapped form in ``tcp6``. The wildcard is written as the
        integer zero address, never as a literal: nothing here binds.
        """
        hexport = f"{self._port:04X}"
        if self._host.version == 6:
            if table == "tcp":
                return frozenset()
            hosts6 = (self._host, ipaddress.IPv6Address(0))
            return frozenset(f"{self._hex6(h)}:{hexport}" for h in hosts6)
        if table == "tcp":
            hosts = (self._host, ipaddress.IPv4Address(0))
            return frozenset(f"{self._hex4(h)}:{hexport}" for h in hosts)
        mapped = ipaddress.IPv6Address(f"::ffff:{self._host}")
        return frozenset({f"{self._hex6(mapped)}:{hexport}"})

    @staticmethod
    def _hex4(host: ipaddress.IPv4Address) -> str:
        """Return *host* as ``/proc/net/tcp`` prints it: one little-endian word."""
        (word,) = struct.unpack("<I", socket.inet_pton(socket.AF_INET, str(host)))
        return f"{word:08X}"

    @staticmethod
    def _hex6(host: ipaddress.IPv6Address) -> str:
        """Return *host* as ``/proc/net/tcp6`` prints it: four little-endian words."""
        words = struct.unpack("<IIII", socket.inet_pton(socket.AF_INET6, str(host)))
        return "".join(f"{word:08X}" for word in words)
