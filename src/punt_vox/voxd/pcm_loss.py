"""Why an output device counts as lost, and what that means for the caller."""

from __future__ import annotations

from enum import Enum

__all__ = ["DeviceLoss"]


class DeviceLoss(Enum):
    """How the device was lost: ``(client-facing text, whether it fails the stream)``.

    A device that stopped or stalled never heard what was still queued. One that
    took longer than the bound to confirm had every block handed to it, so the
    stream is degraded, not failed.
    """

    STOPPED = ("output device stopped before the PCM stream played out", True)
    STALLED = ("output device made no progress; the PCM stream stalled", True)
    UNCONFIRMED = (
        "output device did not confirm within the bound that the last audio played",
        False,
    )

    @property
    def message(self) -> str:
        """Return the text a client reads in the call status."""
        return self.value[0]

    @property
    def is_fatal(self) -> bool:
        """Return whether audio was lost, so the stream failed."""
        return self.value[1]
