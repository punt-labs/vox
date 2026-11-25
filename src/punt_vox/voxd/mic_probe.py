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
from typing import TYPE_CHECKING, Self, final

from punt_vox.types_mic import MicProbeReport
from punt_vox.voxd.types import MessageHandler
from punt_vox.voxd.wire_reply import WireReply

if TYPE_CHECKING:
    from sounddevice import CallbackFlags
    from starlette.websockets import WebSocket

__all__ = ["MicProbeError", "MicProbeHandler", "SoundDeviceCapture"]

logger = logging.getLogger(__name__)

_PROBE_SECONDS = 1.0
_SAMPLE_RATE_HZ = 16_000

type MicCapture = Callable[[float], MicProbeReport]


class MicProbeError(RuntimeError):
    """The default input device could not be opened or read."""


@final
class SoundDeviceCapture:
    """The real capture: record from the default input device via PortAudio.

    Imports ``sounddevice`` inside the call, not at module scope: it is the
    optional ``call`` extra, and every ``voxd`` start imports this module.
    """

    __slots__ = ()

    def __new__(cls) -> Self:
        """Create the capture; it holds no device until called."""
        return super().__new__(cls)

    def __call__(self, seconds: float) -> MicProbeReport:
        """Record *seconds* from the default input device and summarise it.

        Raises:
            MicProbeError: If the extra is absent or PortAudio refuses the device.
        """
        try:
            import sounddevice
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

        try:
            device = sounddevice.query_devices(kind="input")
            with sounddevice.RawInputStream(
                samplerate=float(_SAMPLE_RATE_HZ),
                blocksize=0,
                channels=1,
                dtype="int16",
                callback=_callback,
            ):
                sounddevice.sleep(round(seconds * 1000))
        except sounddevice.PortAudioError as exc:
            raise MicProbeError(str(exc)) from exc
        return self._summarise(device["name"], b"".join(blocks))

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
        logger.info(
            "mic probe: device=%r peak=%d samples=%d",
            report.device,
            report.peak,
            report.samples,
        )
        await reply.send({"type": "mic_result", "result": report.to_wire()})
