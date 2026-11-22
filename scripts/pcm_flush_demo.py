"""Stream speech through voxd's PCM sink and interrupt it mid-sentence.

With ``ELEVENLABS_API_KEY`` set, streams a real ElevenLabs TTS response in
``pcm_16000`` straight into :meth:`PlaybackQueue.stream_pcm`'s sink as the
chunks arrive; without it, a 440 Hz tone stands in. Once the device has
played ``--interrupt-after`` seconds, the sink is flushed -- what an
ElevenLabs ``interruption`` event triggers in a call -- and the voxd log
lines show the flush and what was still queued.

    uv run --extra call python scripts/pcm_flush_demo.py
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import math
import os
import struct
import sys
import time
from collections.abc import Coroutine

import httpx

from punt_vox.voxd.pcm_stream import PCM_SAMPLE_RATE_HZ, PcmStreamSink
from punt_vox.voxd.playback import PlaybackQueue

TEXT = (
    "This is a streamed reply from the voice agent. It keeps talking for a "
    "while, so you can hear it stop the moment you interrupt it, right in "
    "the middle of this sentence, without the rest of it trailing on."
)
VOICE_ID = "21m00Tcm4TlvDq8ikWAM"
TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice}/stream"

logger = logging.getLogger("punt_vox.voxd.pcm_flush_demo")


async def feed_elevenlabs(sink: PcmStreamSink, key: str) -> None:
    started = time.monotonic()
    first = True
    async with (
        httpx.AsyncClient(timeout=30) as client,
        client.stream(
            "POST",
            TTS_URL.format(voice=VOICE_ID),
            params={"output_format": "pcm_16000"},
            headers={"xi-api-key": key},
            json={"text": TEXT, "model_id": "eleven_flash_v2_5"},
        ) as response,
    ):
        response.raise_for_status()
        async for chunk in response.aiter_bytes():
            if first:
                logger.info(
                    "EL first PCM chunk after %.0f ms",
                    (time.monotonic() - started) * 1000,
                )
                first = False
            sink.feed(chunk)
    logger.info("EL stream complete after %.0f ms", (time.monotonic() - started) * 1000)


async def feed_tone(sink: PcmStreamSink) -> None:
    count = PCM_SAMPLE_RATE_HZ * 4
    step = 2 * math.pi * 440 / PCM_SAMPLE_RATE_HZ
    sink.feed(
        b"".join(
            struct.pack("<h", int(12_000 * math.sin(step * i))) for i in range(count)
        )
    )


async def played_past(sink: PcmStreamSink, seconds: float) -> None:
    while sink.played.ms < seconds * 1000:
        await asyncio.sleep(0.005)


async def play_out(sink: PcmStreamSink, feeding: Coroutine[None, None, None]) -> None:
    await feeding
    sink.finish()
    await sink.wait()


async def main(interrupt_after_s: float) -> None:
    key = os.environ.get("ELEVENLABS_API_KEY")
    async with PlaybackQueue().stream_pcm() as sink:
        speech = asyncio.create_task(
            play_out(sink, feed_elevenlabs(sink, key) if key else feed_tone(sink))
        )
        cue = asyncio.create_task(played_past(sink, interrupt_after_s))
        await asyncio.wait({speech, cue}, return_when=asyncio.FIRST_COMPLETED)
        if cue.done():
            logger.info("interruption at %.0f ms of audio played", sink.played.ms)
            sink.flush()
            speech.cancel()
        else:
            cue.cancel()
            logger.info("speech ended before the interruption point")
        with contextlib.suppress(asyncio.CancelledError):
            await speech
        await asyncio.sleep(0.5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Stream speech through the PCM sink and interrupt it."
    )
    parser.add_argument("--interrupt-after", type=float, default=2.0)
    args = parser.parse_args()
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s.%(msecs)03d %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    asyncio.run(main(args.interrupt_after))
