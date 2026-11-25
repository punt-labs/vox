from __future__ import annotations

from collections.abc import Buffer, Callable, Mapping
from typing import Self

class PortAudioError(Exception):
    """Raised when PortAudio refuses an operation, such as opening a device."""

# Only the ``kind="input"`` form this project calls is declared; the real
# function also takes a device index or name and returns a list for ``kind=None``.
def query_devices(*, kind: str) -> Mapping[str, object]: ...
def sleep(msec: int) -> None: ...

# The subset of PortAudio's ``CallbackFlags`` this project inspects: whether
# capture overran the buffer between two callback invocations, which is a
# genuine capture-quality signal a caller may want to log or surface, not a
# fatal condition -- PortAudio keeps delivering audio after an overflow.
class CallbackFlags:
    input_overflow: bool

# The CFFI buffer object ``RawInputStream``'s callback receives as *indata*
# satisfies PEP 688's buffer protocol (``collections.abc.Buffer``, 3.12+);
# only that protocol is used here (``bytes(indata)``), so it is typed
# precisely rather than as ``Any``.
type RawInputCallback = Callable[[Buffer, int, object, CallbackFlags], None]

class RawInputStream:
    """PortAudio input stream on raw buffer objects -- no NumPy dependency.

    Only the constructor keyword arguments and the context-manager protocol
    this project actually calls are declared; the real class accepts more
    (finished_callback, clip_off, ...) that this project never passes.
    """

    def __init__(
        self,
        *,
        samplerate: float,
        blocksize: int,
        channels: int,
        dtype: str,
        callback: RawInputCallback,
    ) -> None: ...
    def __enter__(self) -> Self: ...
    def __exit__(self, *exc_info: object) -> None: ...
    def close(self) -> None: ...

# The CFFI buffer ``RawOutputStream``'s callback fills as *outdata* is writable
# and satisfies the buffer protocol; the sink writes it through ``memoryview``.
type RawOutputCallback = Callable[[Buffer, int, object, CallbackFlags], None]

class RawOutputStream:
    """PortAudio output stream on raw buffer objects -- no NumPy dependency.

    Only what the streaming PCM sink calls is declared: callback mode,
    ``start`` to begin pulling, ``stop`` to drain, ``abort`` to stop without
    draining, and ``finished_callback`` for when the stream goes inactive.
    """

    def __init__(
        self,
        *,
        samplerate: float,
        blocksize: int,
        channels: int,
        dtype: str,
        latency: str,
        callback: RawOutputCallback,
        finished_callback: Callable[[], None],
    ) -> None: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def abort(self) -> None: ...
    def close(self) -> None: ...
