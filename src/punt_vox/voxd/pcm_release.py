"""Release a PCM output stream off the event loop, whatever happens to the caller."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, final

from punt_vox.voxd.pcm_output import DeviceCall

if TYPE_CHECKING:
    from punt_vox.voxd.pcm_output import OutputStream

logger = logging.getLogger(__name__)

__all__ = ["StreamRelease"]


@final
class StreamRelease:
    """Stop or abort an output stream, then always close it, on a worker thread."""

    __slots__ = ()

    @classmethod
    async def run(cls, stream: OutputStream, *, drain: bool) -> None:
        """Release *stream*; re-raise a cancelled caller or a failed release."""
        releasing = await DeviceCall.run(cls._release_device, stream, drain=drain)
        error = releasing.error()
        if releasing.cancelled:
            if error is not None:
                logger.error(
                    "releasing the PCM device during a cancelled close",
                    exc_info=error,
                )
            raise asyncio.CancelledError
        if error is not None:
            raise error

    @classmethod
    async def after_cancelled_open(cls, starting: DeviceCall[OutputStream]) -> None:
        """Close the device a cancelled open was still starting."""
        error = starting.error()
        if error is None:
            releasing = await DeviceCall.run(
                cls._release_device, starting.result(), drain=False
            )
            error = releasing.error()
        if error is not None:
            logger.error(
                "releasing the PCM device after a cancelled open", exc_info=error
            )

    @staticmethod
    def _release_device(stream: OutputStream, *, drain: bool) -> None:
        """Worker thread: stop or abort the stream, then always close it."""
        try:
            if drain:
                stream.stop()
            else:
                stream.abort()
        finally:
            stream.close()
