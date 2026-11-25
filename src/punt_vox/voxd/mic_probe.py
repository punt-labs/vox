"""The ``mic_probe`` RPC: open the daemon's input device briefly and report.

``vox doctor`` runs in the user's shell, which usually holds a microphone
grant the launchd- or systemd-started daemon does not. Capturing in the
doctor process would therefore prove nothing about a call, which captures
inside voxd. So the probe runs here, in the daemon, and the doctor reads the
report over the wire.

The capture is a plain callable so the handler is tested with a fake and
only :class:`SoundDeviceCapture` ever touches hardware.
"""

from __future__ import annotations

import array
import asyncio
import logging
from collections.abc import Buffer, Callable
from typing import TYPE_CHECKING, Protocol, Self, final

from punt_vox.types_mic import MicProbeReport
from punt_vox.voxd.conversation_mode.portaudio import PortAudio
from punt_vox.voxd.types import MessageHandler
from punt_vox.voxd.wire_reply import WireReply

if TYPE_CHECKING:
    from sounddevice import CallbackFlags, RawInputCallback
    from starlette.websockets import WebSocket

    from punt_vox.voxd.conversation_mode.input_device import InputDevice
    from punt_vox.voxd.conversation_mode.mic_audio_source import InputStreamHandle

__all__ = ["MicProbeError", "MicProbeHandler", "ProbeBackend", "SoundDeviceCapture"]

logger = logging.getLogger(__name__)

_PROBE_SECONDS = 1.0
_SAMPLE_RATE_HZ = 16_000

type MicCapture = Callable[[float], MicProbeReport]


class MicProbeError(RuntimeError):
    """The default input device could not be opened or read."""


class ProbeBackend(Protocol):
    """Opens the current default input device as a started raw mono stream.

    The pair returned names the device the stream captures from; the backend
    selects and opens it as one step so the two cannot disagree.
    """

    def open_default_input(
        self, *, samplerate: float, blocksize: int, callback: RawInputCallback
    ) -> tuple[InputDevice, InputStreamHandle]:
        """Return the default input device and a started stream on it, or raise."""
        ...


@final
class SoundDeviceCapture:
    """The real capture: one second from the current default input device.

    The device comes from the call's capture backend, not from a bare
    ``query_devices``: the backend re-reads PortAudio's device table first,
    so the probe sees the microphone connected after voxd started, selects
    and opens the default input under one reservation, so the device the
    report names is the one captured from and a call's refresh cannot
    terminate PortAudio under the probe. Only the input is resolved: a host
    with no usable default output still probes its microphone. ``sounddevice``
    is imported inside the call: it is the optional ``call`` extra, and every
    voxd start imports this module.
    """

    __slots__ = ("_backend",)
    _backend: ProbeBackend

    def __new__(cls, backend: ProbeBackend | None = None) -> Self:  # None: PortAudio
        """Capture through *backend*; voxd's shared PortAudio table by default."""
        self = super().__new__(cls)
        self._backend = PortAudio() if backend is None else backend
        return self

    def __call__(self, seconds: float) -> MicProbeReport:
        """Record *seconds* from the default input device and summarise it.

        Raises:
            MicProbeError: If the extra is absent, the backend has no default
                input device or cannot describe it, or the device refuses
                the stream or will not release it.
        """
        try:
            from sounddevice import PortAudioError, sleep
        except (ImportError, OSError) as exc:
            msg = (
                "microphone capture needs the `call` extra and PortAudio: "
                "install punt-vox[call] and libportaudio2"
            )
            raise MicProbeError(msg) from exc

        blocks: list[bytes] = []

        def _callback(
            indata: Buffer, _frames: int, _time: object, _status: CallbackFlags
        ) -> None:
            blocks.append(bytes(indata))

        # A device name sounddevice cannot decode is a ValueError subclass,
        # not an OSError; both leave the probe with no device to open.
        try:
            device, stream = self._backend.open_default_input(
                samplerate=float(_SAMPLE_RATE_HZ), blocksize=0, callback=_callback
            )
        except (OSError, UnicodeDecodeError) as exc:
            raise MicProbeError(str(exc)) from exc
        # The close is the last PortAudio call; a failure there is the device's
        # error too, and must reach the doctor as one rather than as a dropped
        # connection from the router's catch-all.
        try:
            sleep(round(seconds * 1000))
        finally:
            try:
                stream.__exit__(None, None, None)
            except (OSError, PortAudioError) as exc:
                raise MicProbeError(
                    f"cannot release input device {device.name}: {exc}"
                ) from exc
        return self._summarise(device.name, b"".join(blocks))

    @staticmethod
    def _summarise(device: str, raw: bytes) -> MicProbeReport:
        """Reduce the captured 16-bit mono PCM to the report's peak and count."""
        pcm = array.array("h")
        pcm.frombytes(raw)
        return MicProbeReport(
            device=device,
            sample_rate_hz=_SAMPLE_RATE_HZ,
            peak=max((abs(sample) for sample in pcm), default=0),
            samples=len(pcm),
        )


@final
class MicProbeHandler(MessageHandler):
    """Handle ``mic_probe``: capture one second off the loop and reply."""

    __slots__ = ("_capture",)
    _capture: MicCapture

    def __new__(cls, *, capture: MicCapture | None = None) -> Self:
        """Bind the capture; tests pass a fake, voxd the PortAudio one."""
        self = super().__new__(cls)
        self._capture = SoundDeviceCapture() if capture is None else capture
        return self

    async def __call__(self, msg: dict[str, object], websocket: WebSocket) -> None:
        """Reply ``mic_result`` with the report, or ``error`` with the cause."""
        reply = WireReply(websocket, str(msg.get("id", "")))
        try:
            report = await asyncio.to_thread(self._capture, _PROBE_SECONDS)
        except MicProbeError as exc:
            logger.warning("mic probe failed: %s", exc)
            await reply.send({"type": "error", "message": str(exc)})
            return
        # A probe that heard nothing is the condition the doctor warns about;
        # vox.log should show it at the same level without the doctor's output.
        level = logging.INFO if report.signal else logging.WARNING
        logger.log(
            level,
            "mic probe: device=%r peak=%d samples=%d",
            report.device,
            report.peak,
            report.samples,
        )
        await reply.send({"type": "mic_result", "result": report.to_wire()})
