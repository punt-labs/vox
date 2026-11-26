"""The call's start time, in words the agent can read from its prompt."""

from __future__ import annotations

from datetime import datetime
from typing import Self, final
from zoneinfo import ZoneInfo

from punt_vox.voxd.voice_agent.host_zone import HostZone

__all__ = ["CallClock"]


@final
class CallClock:
    """Name the local date, time and IANA zone when the call starts.

    The agent's own timezone setting is a provision-time guess that goes stale
    when the host travels, so each call states the time itself.
    """

    __slots__ = ("_now",)
    _now: datetime

    def __new__(
        cls,
        now: datetime | None = None,  # None reads the host clock and zone.
    ) -> Self:
        """Bind the start time, which must carry a zone."""
        self = super().__new__(cls)
        self._now = datetime.now(ZoneInfo(HostZone().name())) if now is None else now
        return self

    def line(self) -> str:
        """Return one sentence: weekday, date, time, zone name and UTC offset."""
        tz = self._now.tzinfo
        zone = tz.key if isinstance(tz, ZoneInfo) else "UTC"
        offset = self._now.utcoffset()
        minutes = 0 if offset is None else int(offset.total_seconds() // 60)
        sign = "+" if minutes >= 0 else "-"
        hours, rest = divmod(abs(minutes), 60)
        return (
            f"At the start of this call it is {self._now:%A %Y-%m-%d %H:%M} "
            f"({zone}, UTC{sign}{hours:02d}:{rest:02d})."
        )
