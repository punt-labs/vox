"""An output device that pulls audio on a real-time clock and plays nothing.

A voxd under test on a host with no sound card, or no PortAudio, still has
to open a stream for the agent's voice, feed it, cut it on barge-in and play
it out at the end. This device gives the PCM sink the same callback and
finished-callback contract as PortAudio's, paced at the sample rate on its
own thread, so the sink's played, dropped and interrupted accounting is real
and only the sound is missing. ``VOX_PCM_OUTPUT=silent`` selects it; see
:mod:`punt_vox.voxd.voice_agent.call_overrides`.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.pcm_audio import BYTES_PER_FRAME
from punt_vox.voxd.pcm_output import StreamCompleted

if TYPE_CHECKING:
    from collections.abc import Callable

    from punt_vox.voxd.pcm_output import OutputCallback

__all__ = ["SilentOutputStream"]

logger = logging.getLogger(__name__)


@final
class SilentOutputStream:
    """Pull one block per block period from the sink's callback, discarding it."""

    __slots__ = (
        "_blocksize",
        "_callback",
        "_finished",
        "_period_s",
        "_stop",
        "_thread",
    )
    _callback: OutputCallback
    _finished: Callable[[], None]
    _blocksize: int
    _period_s: float
    _stop: threading.Event
    _thread: threading.Thread

    def __new__(
        cls,
        *,
        samplerate: int,
        blocksize: int,
        callback: OutputCallback,
        finished_callback: Callable[[], None],
    ) -> Self:
        """Build an unstarted stream; this is the :class:`OutputStreamFactory` shape."""
        self = super().__new__(cls)
        self._callback = callback
        self._finished = finished_callback
        self._blocksize = blocksize
        self._period_s = blocksize / samplerate
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        return self

    def start(self) -> None:
        """Begin pulling blocks on the device thread."""
        self._thread.start()

    def stop(self) -> None:
        """Stop after the block in hand; PortAudio's drain has nothing more to play.

        Called off the device thread, as PortAudio requires, and only after
        :meth:`start` succeeded; a thread that died has already left.
        """
        self._stop.set()
        self._thread.join()

    def abort(self) -> None:
        """Stop at once; nothing was buffered beyond the block in hand."""
        self.stop()

    def close(self) -> None:
        """Release the device: the thread is already joined by stop or abort."""
        self._thread.join()

    def _run(self) -> None:
        """Device thread: pull blocks until stopped, then report the stream ended.

        A callback that raises ends the stream, as it does under PortAudio:
        the error is logged and the finished callback still runs, so the
        sink learns the device stopped instead of waiting for it forever.
        """
        try:
            self._drain()
        except StreamCompleted:
            pass  # the last block played: report finished, as PortAudio does
        except Exception:
            logger.exception("silent output device stopped: the callback raised")
        finally:
            self._finished()

    def _drain(self) -> None:
        out = bytearray(self._blocksize * BYTES_PER_FRAME)
        due = time.monotonic()
        while not self._stop.is_set():
            self._callback(out, self._blocksize, None, None)
            due += self._period_s
            self._stop.wait(max(0.0, due - time.monotonic()))
