"""Throwaway proof: track-edge silence before and after EdgeTrim, production lengths."""

import asyncio
import shutil
import sys
from pathlib import Path

from levels import meter
from punt_vox.providers.elevenlabs_music import ElevenLabsMusicProvider
from punt_vox.types_programs.prompts import PromptSet
from punt_vox.voxd.programs.edge_trim import EdgeTrim
from punt_vox.voxd.programs.music_producer import LengthPolicy

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
STYLES = [("lofi", "focused"), ("ambient", "calm"), ("jazz", "relaxed"), ("synthwave", "driving")]


async def main() -> None:
    provider = ElevenLabsMusicProvider()
    policy = LengthPolicy()
    rows = []
    for n, (style, mood) in enumerate(STYLES):
        raw = await provider.generate_track(
            PromptSet.fallback(style, mood).prompt_for(0), policy.sample(), out / f"{n}-{style}-raw.mp3"
        )
        trimmed = out / f"{n}-{style}-trimmed.mp3"
        shutil.copy(raw, trimmed)
        EdgeTrim().apply(trimmed)
        edges = {k: (meter.leading_silence(p), meter.trailing_silence(p), meter.duration(p)) for k, p in (("raw", raw), ("trimmed", trimmed))}
        rows.append(edges)
        print(f"TRACK {style}: raw lead {edges['raw'][0]:.2f} tail {edges['raw'][1]:.2f} dur {edges['raw'][2]:.1f} | trimmed lead {edges['trimmed'][0]:.2f} tail {edges['trimmed'][1]:.2f} dur {edges['trimmed'][2]:.1f}", flush=True)
    for a, b in zip(rows, rows[1:]):
        print(f"GAP raw {a['raw'][1] + b['raw'][0]:.2f}s -> trimmed {a['trimmed'][1] + b['trimmed'][0]:.2f}s", flush=True)


asyncio.run(main())
