"""Throwaway diagnostic: silence at the edges of ElevenLabs tracks."""

import asyncio
import sys
from pathlib import Path

from levels import meter
from punt_vox.providers.elevenlabs_music import ElevenLabsMusicProvider
from punt_vox.types_programs.prompts import PromptSet

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
jobs = [(s, m, ms, i) for (s, m) in [("lofi", "focused"), ("ambient", "calm"), ("jazz", "relaxed")] for ms in (20_000, 90_000) for i in range(2)]


async def main() -> None:
    p = ElevenLabsMusicProvider()
    gate = asyncio.Semaphore(3)

    async def one(s: str, m: str, ms: int, i: int) -> Path:
        async with gate:
            return await p.generate_track(PromptSet.fallback(s, m).prompt_for(0), ms, out / f"{s}-{ms}-{i}.mp3")

    paths = await asyncio.gather(*(one(*j) for j in jobs))
    for path in paths:
        spans = meter.silences(path, 0.05)
        print(f"{path.name}: dur {meter.duration(path):.2f} lead {meter.leading_silence(path):.2f} tail {meter.trailing_silence(path):.2f} lufs {meter.loudness(path).integrated_lufs} internal {[(round(x.start,2), round(x.duration,2)) for x in spans]}")


asyncio.run(main())
