"""Typer option annotations shared by the speech and recording commands."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

__all__ = [
    "FromOpt",
    "JsonOutput",
    "LanguageOpt",
    "ModelOpt",
    "ProviderOpt",
    "RateOpt",
    "SimilarityOpt",
    "SpeakerBoostFlag",
    "StabilityOpt",
    "StyleOpt",
    "TextArg",
    "VoiceOpt",
]

FromOpt = Annotated[
    Path | None,
    typer.Option("--from", help="JSON file with segments array.", exists=True),
]
JsonOutput = Annotated[bool, typer.Option("--json", help="Output JSON.")]
LanguageOpt = Annotated[
    str | None,  # Omitting --language leaves provider language selection unchanged.
    typer.Option("--language", "--lang", help="ISO 639-1 language code (e.g. de, ko)."),
]
ModelOpt = Annotated[
    str | None,
    typer.Option(
        "--model",
        help="Model name (e.g. eleven_v3, tts-1). Provider-specific.",
    ),
]
ProviderOpt = Annotated[
    str | None,
    typer.Option(
        "--provider",
        help=(
            "TTS provider (elevenlabs, polly, openai, say, espeak)."
            " Default: this repo's vox.md provider (vox provider <name>)."
        ),
    ),
]
RateOpt = Annotated[
    int,
    typer.Option(
        "--rate", help="Positive speech rate percentage (e.g. 90 = 90% speed)."
    ),
]
SimilarityOpt = Annotated[
    float | None,
    typer.Option("--similarity", help="ElevenLabs voice similarity boost (0.0-1.0)."),
]
SpeakerBoostFlag = Annotated[
    bool,
    typer.Option("--speaker-boost", help="Enable ElevenLabs speaker boost."),
]
StabilityOpt = Annotated[
    float | None,
    typer.Option("--stability", help="ElevenLabs voice stability (0.0-1.0)."),
]
StyleOpt = Annotated[
    float | None,
    typer.Option("--style", help="ElevenLabs voice style/expressiveness (0.0-1.0)."),
]
TextArg = Annotated[
    str | None, typer.Argument(help="Text to synthesize.", show_default=False)
]
VoiceOpt = Annotated[
    str | None,
    typer.Option("--voice", help="vox.md voice for its provider, or provider default."),
]
