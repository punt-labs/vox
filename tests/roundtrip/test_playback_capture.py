"""Stage five: transcribe the device monitor, not synthesized provider files."""

from __future__ import annotations

import asyncio
import os
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from punt_vox.client import VoxClient
from punt_vox.core import split_text
from punt_vox.normalize import normalize_for_speech
from punt_vox.types_synthesis import SynthesisSpec
from roundtrip.backends import load_whisper, whisper_transcript
from roundtrip.oracle import assert_transcript, decode, words
from roundtrip.playback_capture import (
    capture,
    prerequisite,
    pulse_environment,
    running,
    wait_for_file,
)

pytestmark = pytest.mark.playback

PASSAGES = [
    "The garden gate is open. Please bring the flowers inside.",
    "A quiet river flows through the valley. The birds sing in the morning.",
    (
        "The old library stands beside the road. Every morning the librarian opens "
        "the wooden doors and welcomes the readers."
    ),
]


def assert_markers(transcript: str, markers: list[str]) -> None:
    tokens = words(transcript)
    positions = []
    for marker in markers:
        assert tokens.count(marker) == 1, (
            f"marker {marker!r} missing or duplicated: {transcript}"
        )
        positions.append(tokens.index(marker))
    assert positions == sorted(positions), f"markers reordered: {transcript}"


SEAM = (
    "The captain waits beside the window. "
    "The mountain stands above the village in the bright morning sunshine."
)


def elevenlabs_passage() -> str:
    filler = (
        "The sunlight falls across the tables while people read "
        "their favorite stories. "
    )
    prefix = "The garden gate is open. " + filler * 60
    ending = " The captain waits beside the window."
    prefix += "quiet " * ((4990 - len(prefix) - len(ending)) // 6) + ending
    text = (
        prefix
        + " The mountain stands above the village in the bright morning sunshine. "
        + filler * 6
        + "The library closes in the evening."
    )
    chunks = split_text(normalize_for_speech(text), 5000)
    assert len(chunks) == 2
    assert "captain" in words(chunks[0]) and "mountain" in words(chunks[1])
    return text


def assert_seam(transcript: str) -> None:
    actual = " ".join(words(transcript))
    assert " ".join(words(SEAM)) in actual, (
        f"chunk seam lost or repeated words: {actual}"
    )


@pytest.fixture
def daemon(tmp_path: Path) -> Iterator[tuple[dict[str, str], Path, int, str]]:
    with pulse_environment(tmp_path / "audio") as env:
        home = Path(env["HOME"])
        with running(
            [str(Path(sys.executable).parent / "voxd"), "--port", "0"], env, home
        ) as process:
            run = home / ".punt-labs" / "vox" / "run"
            wait_for_file(run / "serve.port", process)
            wait_for_file(run / "serve.token", process)
            yield (
                env,
                home,
                int((run / "serve.port").read_text()),
                (run / "serve.token").read_text().strip(),
            )


async def verify_capture(
    path: Path, expected: str, markers: list[str], started: float
) -> None:
    audio = decode(path)
    actual = await whisper_transcript(load_whisper(), path)
    error = assert_transcript(normalize_for_speech(expected), actual, 0.05)
    assert_markers(actual, markers)
    if "captain" in markers:
        assert_seam(actual)
    print(
        f"capture duration={len(audio) / 1000:.2f}s "
        f"elapsed={time.monotonic() - started:.2f}s WER={error:.4f}\n"
        f"transcript: {actual}"
    )


async def test_espeak_direct_sequential_order(
    daemon: tuple[dict[str, str], Path, int, str],
) -> None:
    env, home, port, token = daemon
    started = time.monotonic()
    with capture(home, env) as wav:
        async with VoxClient(host="127.0.0.1", port=port, token=token) as client:
            for text in PASSAGES:
                await client.synthesize(
                    text,
                    SynthesisSpec(provider="espeak", voice="en-us", rate=75),
                    timeout=90,
                )
    await verify_capture(
        wav, " ".join(PASSAGES), ["gate", "valley", "library"], started
    )


@pytest.mark.parametrize(
    "provider",
    ["polly", "elevenlabs"],
    ids=["polly-file-playback", "elevenlabs-two-chunk-file-playback"],
)
async def test_live_file_playback_capture(provider: str, tmp_path: Path) -> None:
    required = (
        ["ELEVENLABS_API_KEY"]
        if provider == "elevenlabs"
        else ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_DEFAULT_REGION"]
    )
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        prerequisite("playback credentials missing: " + ", ".join(missing))
    text = " ".join(PASSAGES)
    markers = ["gate", "valley", "library"]
    if provider == "elevenlabs":
        text = elevenlabs_passage()
        markers = ["gate", "captain", "mountain", "library"]
    with pulse_environment(tmp_path / "audio") as env:
        home = Path(env["HOME"])
        with running(
            [str(Path(sys.executable).parent / "voxd"), "--port", "0"], env, home
        ) as process:
            run = home / ".punt-labs" / "vox" / "run"
            wait_for_file(run / "serve.port", process)
            started = time.monotonic()
            wall_started = time.time()
            with capture(home, env) as wav:
                async with VoxClient(
                    host="127.0.0.1",
                    port=int((run / "serve.port").read_text()),
                    token=(run / "serve.token").read_text().strip(),
                ) as client:
                    await client.synthesize(
                        text,
                        SynthesisSpec(
                            provider=provider,
                            model="eleven_v3" if provider == "elevenlabs" else None,
                        ),
                        timeout=120,
                    )
                    async with VoxClient(
                        host="127.0.0.1",
                        port=int((run / "serve.port").read_text()),
                        token=(run / "serve.token").read_text().strip(),
                    ) as observer:
                        async with asyncio.timeout(480):
                            while True:
                                result = (await observer.health()).last_playback
                                if result and float(str(result["ts"])) >= wall_started:
                                    assert result["rc"] == 0, result
                                    break
                                await asyncio.sleep(0.5)
            await verify_capture(wav, text, markers, started)
