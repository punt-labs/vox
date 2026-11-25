from __future__ import annotations

from collections.abc import Buffer, Callable, Sequence
from typing import Literal, NotRequired, Self, TypedDict, overload

# The two re-enumeration functions are exported by name: sounddevice keeps
# them private, but they are the only way to re-read PortAudio's device
# table, and declaring them here is what makes that use a typed one.
__all__ = [
    "CallbackFlags",
    "DeviceInfo",
    "PortAudioError",
    "RawInputCallback",
    "RawInputStream",
    "RawOutputCallback",
    "RawOutputStream",
    "_initialize",
    "_terminate",
    "query_devices",
    "sleep",
]

class PortAudioError(Exception):
    """Raised by sounddevice when PortAudio reports an error."""

# The three keys of ``query_devices``'s per-device dict this project reads;
# the real dict carries hostapi, channel counts and latencies too.
class DeviceInfo(TypedDict):
    name: str
    # Always present in the real dict; NotRequired so test doubles that
    # describe a device by name and rate alone still type-check.
    index: NotRequired[int]
    default_samplerate: float

# Without arguments: every device PortAudio enumerated (a ``DeviceList``,
# a list subclass). With ``kind``: the default device of that kind. With an
# index: that one device, as the current table describes it.
@overload
def query_devices() -> Sequence[DeviceInfo]: ...
@overload
def query_devices(kind: Literal["input", "output"]) -> DeviceInfo: ...
@overload
def query_devices(device: int) -> DeviceInfo: ...

# PortAudio enumerates devices in Pa_Initialize and never again; sounddevice
# wraps the two calls as these private functions and offers no public way to
# re-read the table, so the daemon's DeviceTable calls them in this order.
def _terminate() -> None: ...
def _initialize() -> None: ...

# Block the calling thread for *msec* milliseconds while a stream's callback
# keeps running; the mic probe uses it to hold its input stream open.
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

    # ``device`` binds the stream to one device index; omitted, PortAudio
    # opens whatever its table calls the default input at that moment.
    def __init__(
        self,
        *,
        samplerate: float,
        blocksize: int,
        channels: int,
        dtype: str,
        callback: RawInputCallback,
        device: int | None = None,
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
