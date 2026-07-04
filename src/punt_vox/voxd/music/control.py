"""The music loop's control channel: ownership plus the pending-action signal.

The scheduler's domain commands write control actions here; the loop reads them
at each control point. Ownership gates who may change the vibe or stop playback:
a command is honoured only from the current owner, and turn-off / disable release
ownership so a stale forwarded message cannot silently restart playback or fill.
Splitting this out of :class:`~punt_vox.voxd.music.scheduler.MusicScheduler`
isolates the sync/ownership concern from session state and command handling
(PY-IC-6).
"""

from __future__ import annotations

import asyncio
from typing import Literal, Self

__all__ = ["MusicControl", "MusicControlChannel"]

# The pending playback action the loop reads at the next control point.
MusicControl = Literal["none", "off", "skip", "play", "vibe"]


class MusicControlChannel:
    """Ownership, the on/off lifecycle, and the loop's pending-action signal."""

    __slots__ = ("_active", "_changed", "_control", "_owner")

    _active: bool
    _changed: asyncio.Event
    _control: MusicControl
    _owner: str

    def __new__(cls) -> Self:
        self = super().__new__(cls)
        self._owner = ""
        self._control = "none"
        self._active = False
        self._changed = asyncio.Event()
        return self

    @property
    def owner(self) -> str:
        """Return the current music owner session ID (empty when unowned)."""
        return self._owner

    @property
    def active(self) -> bool:
        """Return whether music is currently on."""
        return self._active

    @property
    def changed(self) -> asyncio.Event:
        """Return the control-signal event the loop races against playback."""
        return self._changed

    def owned_by(self, owner_id: str) -> bool:
        """Return whether ``owner_id`` currently owns music."""
        return bool(owner_id) and self._owner == owner_id

    def activate(self) -> None:
        """Mark music on (the loop's ``wait_active`` unblocks on the next wake)."""
        self._active = True

    def deactivate(self) -> None:
        """Mark music off so the loop returns to waiting."""
        self._active = False

    async def wait_active(self) -> None:
        """Block until music is turned on."""
        while not self._active:
            await self._changed.wait()
            self._changed.clear()

    def claim(self, owner_id: str) -> None:
        """Record ``owner_id`` as the music owner."""
        self._owner = owner_id

    def release(self) -> None:
        """Clear ownership so no stale forwarded message is accepted as owner."""
        self._owner = ""

    def signal(self, control: MusicControl) -> None:
        """Record a pending control action and wake the loop."""
        self._control = control
        self._changed.set()

    def take(self) -> MusicControl:
        """Return the pending control action and reset it to 'none'."""
        control = self._control
        self._control = "none"
        return control
