"""Watch the output device from the event loop: has it stalled, did it play out."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Self, final

from punt_vox.voxd.pcm_loss import DeviceLoss
from punt_vox.voxd.pcm_output import OutputDeviceLostError

logger = logging.getLogger(__name__)

__all__ = ["DeviceWatch"]

STALL_WAIT_S = 1.0
"""A device that has made no callback for this long is stalled, not slow."""

PLAYOUT_WAIT_S = 0.5
"""How long a released stream may take to play what the device holds.

PortAudio's ``low`` latency holds tens of milliseconds, so this is generous;
a device that has not reported finished by then has stopped consuming."""


@final
class DeviceWatch:
    """What the loop knows about the device: callbacks seen, finished, lost.

    The device thread calls :meth:`tick`, :meth:`finished` and :meth:`lose`;
    the loop awaits :meth:`until` and :meth:`played_out`. A device that stops
    answering is only ever abandoned here, never waited on: the caller then
    releases it with ``abort()``, which does not wait for the device.
    """

    __slots__ = ("_callbacks", "_loop", "_loss", "_played_out")

    _callbacks: int
    _loop: asyncio.AbstractEventLoop
    _loss: DeviceLoss | None  # None while the device is healthy.
    _played_out: asyncio.Event

    def __new__(cls, loop: asyncio.AbstractEventLoop) -> Self:
        """Watch a device whose events are delivered on *loop*."""
        self = super().__new__(cls)
        self._callbacks = 0
        self._loop = loop
        self._loss = None
        self._played_out = asyncio.Event()
        return self

    @property
    def loss(self) -> DeviceLoss | None:
        """Return why the device was lost first, or None while it is healthy."""
        return self._loss

    def tick(self) -> None:
        """Device thread: one callback ran."""
        self._callbacks += 1

    def lose(self, loss: DeviceLoss) -> None:
        """Mark the device lost; the first cause stays."""
        if self._loss is None:
            self._loss = loss

    def finished(self) -> None:
        """Device thread: the stream finished having played its last block."""
        self.signal(self._played_out)

    def signal(self, event: asyncio.Event) -> None:
        """Device thread: set *event* on the loop; a closed loop at shutdown is fine."""
        with contextlib.suppress(RuntimeError):
            self._loop.call_soon_threadsafe(event.set)

    async def until(self, done: asyncio.Event) -> None:
        """Wait for *done*; raise if the device makes no callback for a period.

        The check runs once per period and compares callback counts, so it
        fires after one to two periods of silence, and never on a slow device.
        """
        calls = -1
        while not done.is_set():
            try:
                async with asyncio.timeout(STALL_WAIT_S):
                    await done.wait()
            except TimeoutError:
                if self._callbacks == calls:
                    logger.warning(
                        "PCM output made no callback for at least %.3g s; "
                        "giving up the wait",
                        STALL_WAIT_S,
                    )
                    self._loss = self._loss or DeviceLoss.STALLED
                    raise OutputDeviceLostError(DeviceLoss.STALLED.message) from None
                calls = self._callbacks

    async def played_out(self) -> bool:
        """Wait, bounded, for the device to report the stream played out."""
        try:
            async with asyncio.timeout(PLAYOUT_WAIT_S):
                await self._played_out.wait()
        except TimeoutError:
            logger.warning(
                "PCM device did not finish playing within %.1f s; aborting it",
                PLAYOUT_WAIT_S,
            )
            self._loss = self._loss or DeviceLoss.UNCONFIRMED
            return False
        return True
