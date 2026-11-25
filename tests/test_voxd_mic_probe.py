"""Tests for punt_vox.voxd.mic_probe -- the daemon-side microphone probe RPC.

No test opens an audio device: the handler takes an injectable capture
function and every test supplies a fake.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from punt_vox.types_mic import MicProbeReport
from punt_vox.voxd.mic_probe import MicProbeError, MicProbeHandler

if TYPE_CHECKING:
    from starlette.websockets import WebSocket


class _CollectingWs:
    """A fake websocket that records the frames the handler sends."""

    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []

    async def send_json(self, data: dict[str, object]) -> None:
        self.sent.append(data)


def _report(peak: int) -> MicProbeReport:
    return MicProbeReport(
        device="Fake Mic", sample_rate_hz=16_000, peak=peak, samples=16_000
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("peak", [0, 812])
async def test_probe_replies_with_the_capture_report(peak: int) -> None:
    """Silence and signal both cross as a ``mic_result`` carrying the report."""
    seconds_seen: list[float] = []

    def capture(seconds: float) -> MicProbeReport:
        seconds_seen.append(seconds)
        return _report(peak)

    ws = _CollectingWs()
    await MicProbeHandler(capture=capture)(
        {"type": "mic_probe", "id": "m1"}, cast("WebSocket", ws)
    )

    assert seconds_seen == [1.0]
    (frame,) = ws.sent
    assert frame["type"] == "mic_result"
    assert frame["id"] == "m1"
    report = MicProbeReport.from_wire(frame["result"])
    assert report == _report(peak)
    assert report.signal == (peak > 0)


@pytest.mark.asyncio
async def test_unopenable_device_crosses_as_an_error_frame() -> None:
    """A device the daemon cannot open answers with the error text, not a teardown."""

    def capture(_seconds: float) -> MicProbeReport:
        raise MicProbeError("Error opening RawInputStream: Internal PortAudio error")

    ws = _CollectingWs()
    await MicProbeHandler(capture=capture)(
        {"type": "mic_probe", "id": "m2"}, cast("WebSocket", ws)
    )

    (frame,) = ws.sent
    assert frame["type"] == "error"
    assert frame["id"] == "m2"
    assert "Internal PortAudio error" in str(frame["message"])


@pytest.mark.asyncio
async def test_missing_call_extra_names_the_fix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without sounddevice the default capture names the extra instead of crashing."""
    import sys

    from punt_vox.voxd.mic_probe import SoundDeviceCapture

    monkeypatch.setitem(sys.modules, "sounddevice", None)
    with pytest.raises(MicProbeError, match=r"punt-vox\[call\]"):
        SoundDeviceCapture()(0.01)
