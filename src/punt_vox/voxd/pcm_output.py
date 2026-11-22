"""The output device a streaming PCM sink drives, and the real PortAudio one.

:class:`~punt_vox.voxd.pcm_stream.PcmStreamSink` talks to the device only
through :class:`OutputStream`, opened by an :class:`OutputStreamFactory`, so
tests inject an in-memory device and voxd injects
:func:`sounddevice_output_stream`.
"""

from __future__ import annotations

from collections.abc import Buffer, Callable
from typing import Protocol

__all__ = [
    "OutputCallback",
    "OutputDeviceLostError",
    "OutputStream",
    "OutputStreamFactory",
    "sounddevice_output_stream",
]


class OutputDeviceLostError(RuntimeError):
    """The output device stopped before the stream ended."""


class OutputStream(Protocol):
    """The slice of a PortAudio raw output stream the sink drives."""

    def start(self) -> None:
        """Begin invoking the callback."""
        ...

    def stop(self) -> None:
        """Stop after the device has played every block already pulled."""
        ...

    def abort(self) -> None:
        """Stop at once, discarding what the device has not yet played."""
        ...

    def close(self) -> None:
        """Release the device."""
        ...


type OutputCallback = Callable[[Buffer, int, object, object], None]


class OutputStreamFactory(Protocol):
    """Open a mono int16 output stream that pulls audio through *callback*.

    *finished_callback* runs on the device thread once the stream stops,
    whether voxd stopped it or the device went away.
    """

    def __call__(
        self,
        *,
        samplerate: int,
        blocksize: int,
        callback: OutputCallback,
        finished_callback: Callable[[], None],
    ) -> OutputStream:
        """Return an unstarted stream."""
        ...


def sounddevice_output_stream(
    *,
    samplerate: int,
    blocksize: int,
    callback: OutputCallback,
    finished_callback: Callable[[], None],
) -> OutputStream:
    """Open the default PortAudio output device.

    Imports ``sounddevice`` here, not at module scope: it is the optional
    ``call`` extra, and every ``voxd`` start imports this module.
    """
    try:
        from sounddevice import RawOutputStream
    except (ImportError, OSError) as exc:
        msg = (
            "streaming playback needs the `call` extra and PortAudio: "
            "install punt-vox[call] and libportaudio2"
        )
        raise RuntimeError(msg) from exc

    return RawOutputStream(
        samplerate=samplerate,
        blocksize=blocksize,
        channels=1,
        dtype="int16",
        latency="low",
        callback=callback,
        finished_callback=finished_callback,
    )
