"""Domain types for punt-vox."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum, StrEnum
from pathlib import Path
from typing import Protocol, runtime_checkable

from punt_vox.types_audio import AudioRequest, AudioResult
from punt_vox.types_errors import VoiceNotFoundError

logger = logging.getLogger(__name__)

__all__ = [
    "SUPPORTED_LANGUAGES",
    "AudioProvider",
    "AudioProviderId",
    "AudioRequest",
    "AudioResult",
    "DirectPlayProvider",
    "HealthCheck",
    "MergeStrategy",
    "MusicProvider",
    "MusicRequest",
    "MusicResult",
    "SynthesisRequest",
    "SynthesisResult",
    "TTSProvider",
    "VoiceNotFoundError",
    "generate_filename",
    "result_to_dict",
    "validate_language",
]


class AudioProviderId(StrEnum):
    """Supported text-to-speech providers."""

    elevenlabs = "elevenlabs"
    polly = "polly"
    openai = "openai"
    say = "say"
    espeak = "espeak"


@runtime_checkable
class AudioProvider(Protocol):
    """Provider that synthesizes audio from text."""

    def generate_audio(self, request: AudioRequest) -> AudioResult: ...

    def generate_audios(
        self, requests: Sequence[AudioRequest]
    ) -> list[AudioResult]: ...


# ISO 639-1 codes for common language-learning languages.
# Reference data — not a validation whitelist. Any valid ISO 639-1
# code is accepted; providers decide what they support.
SUPPORTED_LANGUAGES: dict[str, str] = {
    "ar": "Arabic",
    "bn": "Bengali",
    "ca": "Catalan",
    "cs": "Czech",
    "cy": "Welsh",
    "da": "Danish",
    "de": "German",
    "el": "Greek",
    "en": "English",
    "es": "Spanish",
    "fi": "Finnish",
    "fr": "French",
    "he": "Hebrew",
    "hi": "Hindi",
    "hu": "Hungarian",
    "id": "Indonesian",
    "it": "Italian",
    "ja": "Japanese",
    "ko": "Korean",
    "ms": "Malay",
    "nb": "Norwegian Bokmål",
    "nl": "Dutch",
    "pl": "Polish",
    "pt": "Portuguese",
    "ro": "Romanian",
    "ru": "Russian",
    "sk": "Slovak",
    "sv": "Swedish",
    "th": "Thai",
    "tr": "Turkish",
    "uk": "Ukrainian",
    "vi": "Vietnamese",
    "zh": "Chinese",
}


validate_language = AudioRequest.validate_language


@dataclass(frozen=True)
class HealthCheck:
    """Result of a single health check."""

    passed: bool
    message: str
    required: bool = field(default=True)


class MergeStrategy(Enum):
    """Controls whether batch operations produce one file per input or one
    merged file for the entire batch."""

    ONE_FILE_PER_INPUT = "separate"
    ONE_FILE_PER_BATCH = "single"


SynthesisRequest = AudioRequest
SynthesisResult = AudioResult


result_to_dict = AudioResult.to_dict


@runtime_checkable
class TTSProvider(AudioProvider, Protocol):
    """Provider-agnostic interface for text-to-speech engines."""

    @property
    def name(self) -> str:
        """Short identifier for this provider (e.g. 'polly')."""
        ...

    @property
    def default_voice(self) -> str:
        """Default voice name for this provider."""
        ...

    @property
    def supports_expressive_tags(self) -> bool:
        """Whether this provider interprets [bracketed] tags as performance cues."""
        ...

    def synthesize(self, request: AudioRequest, output_path: Path) -> AudioResult:
        """Synthesize text to an audio file.

        Args:
            request: The synthesis parameters.
            output_path: Where to write the audio file.

        Returns:
            A SynthesisResult with the path and metadata.
        """
        ...

    def resolve_voice(self, name: str, language: str | None = None) -> str:
        """Validate and resolve a voice name.

        Args:
            name: Case-insensitive voice name.
            language: Optional ISO 639-1 code for compatibility check.

        Returns:
            The canonical voice name.

        Raises:
            ValueError: If the voice is invalid or incompatible with language.
        """
        ...

    def get_default_voice(self, language: str) -> str:
        """Get the default voice for a language.

        Args:
            language: ISO 639-1 code (e.g. 'de', 'ko').

        Returns:
            Voice name suitable for the language.

        Raises:
            ValueError: If no voice is available for this language.
        """
        ...

    def list_voices(self, language: str | None = None) -> list[str]:
        """List available voices, optionally filtered by language.

        Args:
            language: ISO 639-1 code to filter by, or None for all.

        Returns:
            Sorted list of voice names.
        """
        ...

    def infer_language_from_voice(self, voice: str) -> str | None:
        """Infer language from a voice name (best-effort).

        Returns:
            ISO 639-1 code if inferrable, else None.
        """
        ...

    def check_health(self) -> list[HealthCheck]:
        """Run provider-specific health checks.

        Returns:
            List of HealthCheck results.
        """
        ...


@runtime_checkable
class DirectPlayProvider(Protocol):
    """Optional capability: synthesize and play in one step.

    Providers that can play to the default audio device without producing
    an intermediate file (espeak-ng, macOS say) implement this protocol.
    Cloud providers (ElevenLabs, OpenAI, Polly) deliberately do not, so
    their MP3 output keeps flowing through the cache and dedup pipeline.

    Callers use ``isinstance(provider, DirectPlayProvider)`` to test the
    capability at runtime; the protocol is ``@runtime_checkable``.
    """

    def play_directly(self, request: AudioRequest) -> int:
        """Synthesize and play, returning the subprocess exit code."""
        ...


@dataclass(frozen=True)
class MusicRequest:
    """Request to generate a music track."""

    prompt: str
    duration_ms: int
    style: str | None = None
    vibe: str | None = None
    vibe_tags: str | None = None


@dataclass(frozen=True)
class MusicResult:
    """Result of a music generation request."""

    path: Path
    duration_ms: int
    prompt: str


@runtime_checkable
class MusicProvider(Protocol):
    """Provider-agnostic interface for music generation engines."""

    async def generate_track(
        self, prompt: str, duration_ms: int, output_path: Path
    ) -> Path:
        """Generate a music track and write it to output_path.

        Args:
            prompt: Descriptive prompt for the track.
            duration_ms: Desired track length in milliseconds.
            output_path: Where to write the audio file.

        Returns:
            The path to the generated file.
        """
        ...


generate_filename = AudioRequest.generate_filename
