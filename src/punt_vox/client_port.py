"""Decimal TCP port parsing shared by discovery and lifecycle probes."""

from __future__ import annotations

from typing import final

__all__ = ["PortText"]


@final
class PortText:
    """The decimal text representation of a TCP port."""

    __slots__ = ()

    @staticmethod
    def parse(text: str) -> int:
        """Return a port in 1-65535, or raise ``ValueError``."""
        preview = f"{text[:32]!r}{'...' if len(text) > 32 else ''}"
        msg = f"{preview} is not a port (1-65535)"
        if not text.isdecimal():
            raise ValueError(msg)
        try:
            port = int(text)
        except ValueError:
            raise ValueError(msg) from None
        if not 1 <= port <= 65535:
            raise ValueError(msg)
        return port
