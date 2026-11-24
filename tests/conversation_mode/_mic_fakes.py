"""A fake PortAudio input stream for :mod:`.mic_audio_source`.

No real audio hardware, no ``sounddevice`` C extension calls -- the
callback :class:`MicAudioSource` registers is invoked directly by
:meth:`FakeInputStream.feed`, the same way a real PortAudio callback thread
would invoke it, but synchronously and under the test's control.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.conversation_mode.input_device import InputDevice

__all__ = [
    "FAKE_DEVICE",
    "FakeBackend",
    "FakeCallbackFlags",
    "FakeInputStream",
]

if TYPE_CHECKING:
    from collections.abc import Callable

FAKE_DEVICE = InputDevice("Fake Microphone", 48_000.0)


@final
class FakeCallbackFlags:
    """Stand-in for :class:`sounddevice.CallbackFlags`."""

    __slots__ = ("_input_overflow",)
    _input_overflow: bool

    def __new__(cls, *, input_overflow: bool = False) -> Self:
        self = super().__new__(cls)
        self._input_overflow = input_overflow
        return self

    @property
    def input_overflow(self) -> bool:
        return self._input_overflow


@final
class FakeInputStream:
    """Records construction kwargs; :meth:`feed` drives the registered callback."""

    __slots__ = ("_callback", "_closed", "_kwargs")
    _callback: object
    _kwargs: dict[str, object]
    _closed: bool

    def __new__(cls, **kwargs: object) -> Self:
        self = super().__new__(cls)
        self._kwargs = kwargs
        self._callback = kwargs["callback"]
        self._closed = False
        return self

    @property
    def closed(self) -> bool:
        """Return whether ``__exit__`` has run -- the device was released."""
        return self._closed

    @property
    def kwargs(self) -> dict[str, object]:
        """Return the keyword arguments the factory was constructed with."""
        return dict(self._kwargs)

    def feed(self, pcm: bytes, *, input_overflow: bool = False) -> None:
        """Invoke the registered callback with *pcm*, as PortAudio's thread would."""
        callback = self._callback
        assert callable(callback)
        flags = FakeCallbackFlags(input_overflow=input_overflow)
        callback(pcm, len(pcm) // 2, None, flags)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._closed = True


@final
class FakeBackend:
    """A capture backend that reports :data:`FAKE_DEVICE` and records streams.

    ``created`` accumulates streams in construction order, so a test can
    reach the most recent one to :meth:`~FakeInputStream.feed` it once the
    generator under test has reached its first ``await``.
    """

    __slots__ = ("_created", "_open")
    _created: list[FakeInputStream]
    _open: Callable[..., FakeInputStream]

    def __new__(
        cls,
        created: list[FakeInputStream] | None = None,  # None: a private list.
        *,
        open_stream: Callable[..., FakeInputStream] | None = None,  # None: a fake.
    ) -> Self:
        self = super().__new__(cls)
        self._created = created if created is not None else []
        self._open = open_stream if open_stream is not None else FakeInputStream
        return self

    @property
    def created(self) -> list[FakeInputStream]:
        """Return the streams opened so far, oldest first."""
        return self._created

    def device(self) -> InputDevice:
        return FAKE_DEVICE

    def open(self, **kwargs: object) -> FakeInputStream:
        stream = self._open(**kwargs)
        self._created.append(stream)
        return stream
