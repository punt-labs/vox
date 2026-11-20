"""Local Whisper and the production Scribe adapter share a file interface."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from functools import cache, partial
from pathlib import Path
from typing import Protocol, cast

import pytest

from punt_vox.providers.elevenlabs_stt import ElevenLabsSTTProvider
from punt_vox.voxd.conversation_mode.audio_chunk import AudioChunk
from roundtrip.oracle import decode


class Segment(Protocol):
    text: str


class WhisperModel(Protocol):
    def transcribe(
        self,
        audio: str,
        *,
        language: str,
        beam_size: int,
        temperature: float,
        condition_on_previous_text: bool,
        vad_filter: bool,
    ) -> tuple[Iterable[Segment], object]: ...


@cache
def load_whisper() -> WhisperModel:
    whisper = pytest.importorskip("faster_whisper", reason="install uv group roundtrip")
    hub = pytest.importorskip("huggingface_hub", reason="install uv group roundtrip")
    model_path = hub.snapshot_download(
        "Systran/faster-whisper-small.en",
        revision="d1d751a5f8271d482d14ca55d9e2deeebbae577f",
        allow_patterns=["model.bin", "config.json", "tokenizer.json", "vocabulary.*"],
    )
    return cast(
        "WhisperModel",
        whisper.WhisperModel(
            model_path, device="cpu", compute_type="int8", cpu_threads=2, num_workers=1
        ),
    )


async def whisper_transcript(model: WhisperModel, path: Path) -> str:
    segments, _ = model.transcribe(
        str(path),
        language="en",
        beam_size=5,
        temperature=0.0,
        condition_on_previous_text=False,
        vad_filter=False,
    )
    return " ".join(segment.text for segment in segments)


def transcriber(backend: str) -> Callable[[Path], Awaitable[str]]:
    if backend == "scribe":
        return scribe_transcript
    if backend == "whisper":
        return partial(whisper_transcript, load_whisper())
    raise ValueError(f"Unknown transcription backend: {backend}")


async def scribe_transcript(path: Path) -> str:
    audio = decode(path)
    chunk = AudioChunk(pcm=audio.raw_data, duration_s=len(audio) / 1000)
    return " ".join(
        [
            event.text
            async for event in ElevenLabsSTTProvider().transcribe(
                AudioChunk.as_async_iter([chunk])
            )
            if event.is_final
        ]
    )
