"""Measure intelligibility without a system daemon or audio device."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol, cast

from pydub import AudioSegment

from punt_vox.core import TTSClient, split_text
from punt_vox.normalize import normalize_for_speech
from punt_vox.types import MergeStrategy, SynthesisRequest, TTSProvider

THRESHOLD = 0.02
# espeak-ng en-us at 131 WPM / small.en CPU int8 WER: garden 0, river 0,
# identifiers 0, two-chunk passage 1/74 (.0135); .02 leaves .0065 margin.
# The shortest reference has 10 words: one wrong word costs .10 > .02.


class AudioLevels(Protocol):
    @property
    def rms(self) -> int: ...


def words(text: str) -> list[str]:
    return re.findall(r"[a-z]+", text.casefold())


def word_error_rate(expected: str, actual: str) -> float:
    reference, hypothesis = words(expected), words(actual)
    if not reference:
        return float(bool(hypothesis))
    previous = list(range(len(hypothesis) + 1))
    for i, left in enumerate(reference, 1):
        current = [i]
        for j, right in enumerate(hypothesis, 1):
            current.append(
                min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (left != right))
            )
        previous = current
    return previous[-1] / len(reference)


def assert_transcript(expected: str, actual: str, threshold: float) -> float:
    error = word_error_rate(expected, actual)
    assert words(expected), "oracle requires a nonempty reference"
    assert error <= threshold, f"WER={error:.3f} > {threshold:.3f}: {actual!r}"
    return error


def decode(path: Path) -> AudioSegment:
    audio = AudioSegment.from_file(str(path))
    assert len(audio) > 0, "decoded audio has zero duration"
    audio = audio.set_frame_rate(16000).set_channels(1).set_sample_width(2)
    levels = cast("AudioLevels", audio)
    assert levels.rms > 32, "decoded audio is silent"
    return audio


def synthesize(provider: TTSProvider, text: str, directory: Path) -> Path:
    spoken = normalize_for_speech(text)
    chunks = split_text(spoken, max_chars=240)
    requests = [
        SynthesisRequest(text=chunk, language="en", voice="en-us", rate=75)
        if provider.name == "espeak"
        else SynthesisRequest(text=chunk, language="en", rate=90)
        for chunk in chunks
    ]
    results = TTSClient(provider).synthesize_batch(
        requests, directory, MergeStrategy.ONE_FILE_PER_BATCH, pause_ms=150
    )
    return results[0].path
