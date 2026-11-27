"""The host's IANA time zone name, read from the host and never hardcoded."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Final, Self, final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["HostZone"]

_LOCALTIME: Final = "/etc/localtime"
_MARKER: Final = "zoneinfo/"
_FALLBACK: Final = "UTC"


@final
class HostZone:
    """Name the zone the host runs in, as the agent's `timezone` setting wants it.

    An abbreviation such as PDT is wrong half the year, so only an IANA name
    (``America/Los_Angeles``) is ever returned: from the ``/etc/localtime``
    link, else a valid ``TZ``, else UTC.
    """

    __slots__ = ("_environ", "_localtime")
    _localtime: Path
    _environ: Mapping[str, str]

    def __new__(
        cls,
        localtime: Path | None = None,  # None reads /etc/localtime.
        environ: Mapping[str, str] | None = None,  # None reads the process env.
    ) -> Self:
        """Bind where to read the zone from."""
        self = super().__new__(cls)
        self._localtime = Path(_LOCALTIME) if localtime is None else localtime
        self._environ = os.environ if environ is None else environ
        return self

    def name(self) -> str:
        """Return the host's IANA zone name."""
        return self._from_link() or self._from_environment() or _FALLBACK

    def _from_link(self) -> str | None:
        try:
            target = str(self._localtime.resolve(strict=True))
        except OSError:
            return None
        _, found, name = target.partition(_MARKER)
        return name if found and self.is_iana(name) else None

    def _from_environment(self) -> str | None:
        name = self._environ.get("TZ", "").lstrip(":")
        return name if self.is_iana(name) else None

    @staticmethod
    def is_iana(name: str) -> bool:
        """Return whether ``name`` is an IANA zone name or UTC, not an abbreviation."""
        try:
            ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError, OSError):
            return False
        return "/" in name or name == _FALLBACK
