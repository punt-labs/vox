"""Domain types for punt-vox."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum, StrEnum
from pathlib import Path
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)

__all__ = [
    "SUPPORTED_LANGUAGES",
    "AudioProvider",
    "AudioProviderId",
    "AudioRequest",
    "AudioResult",
    "HealthCheck",
    "MergeStrategy",
    "SynthesisRequest",
    "SynthesisResult",
    "TTSProvider",
    "VoiceNotFoundError",
    "generate_filename",
    "result_to_dict",
    "validate_language",
]


class VoiceNotFoundError(ValueError):
    """Raised when a voice name cannot be resolved by a provider."""

    def __init__(self, name: str, available: list[str]) -> None:
        self.voice_name = name
        self.available = available
        super().__init__(name)


def _metadata() -> dict[str, str]:
    return {}


class AudioProviderId(StrEnum):
    """Supported text-to-speech providers."""

    elevenlabs = "elevenlabs"
    polly = "polly"
    openai = "openai"
    say = "say"
    espeak = "espeak"


@dataclass(frozen=True)
class AudioRequest:
    """Request to synthesize a single audio clip."""

    text: str
    voice: str | None = None
    language: str | None = None
    rate: int | None = None
    stability: float | None = None
    similarity: float | None = None
    style: float | None = None
    speaker_boost: bool | None = None
    provider: AudioProviderId | None = None
    metadata: dict[str, str] = field(default_factory=_metadata)


@dataclass(frozen=True)
class AudioResult:
    """Result of an audio synthesis request."""

    path: Path
    text: str
    provider: AudioProviderId
    voice: str | None = None
    language: str | None = None
    metadata: dict[str, str] = field(default_factory=_metadata)


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


def validate_language(code: str) -> str:
    """Validate and normalize an ISO 639-1 language code.

    Checks format only (2 lowercase ASCII letters). Does not check
    whether the code is in SUPPORTED_LANGUAGES — providers decide
    what they support.

    Returns:
        The lowercase code.

    Raises:
        ValueError: If the code is not 2 ASCII letters.
    """
    normalized = code.strip().lower()
    if len(normalized) != 2 or not normalized.isascii() or not normalized.isalpha():
        msg = (
            f"Invalid language code '{code}'. "
            "Expected ISO 639-1 format (2 letters, e.g. 'de', 'ko')."
        )
        raise ValueError(msg)
    return normalized


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


def result_to_dict(result: AudioResult) -> dict[str, str]:
    """Serialize AudioResult to a dict suitable for MCP tool responses."""
    d: dict[str, str] = {
        "path": str(result.path),
        "text": result.text,
        "provider": result.provider.value,
    }
    if result.voice is not None:
        d["voice"] = result.voice
    if result.language is not None:
        d["language"] = result.language
    if result.metadata:
        d.update(result.metadata)
    return d


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

    def play_directly(self, request: AudioRequest) -> int | None:
        """Synthesize and play audio in one step, bypassing the file pipeline.

        Local providers (espeak-ng, macOS say) play directly to the
        default audio device. Cloud providers return ``None`` because
        their MP3 output benefits from caching for dedup replay.

        Returns:
            The subprocess exit code (0 on success), or ``None`` if direct
            play is not supported -- caller falls back to ``synthesize``
            + playback queue.
        """
        ...


def generate_filename(text: str, prefix: str = "") -> str:
    """Generate a deterministic MP3 filename from text content.

    Uses an MD5 hash of the text to produce a stable, filesystem-safe
    filename. An optional prefix is prepended for disambiguation.

    Args:
        text: The source text.
        prefix: Optional prefix (e.g. "pair_").

    Returns:
        A filename like "a1b2c3d4.mp3" or "pair_a1b2c3d4.mp3".
    """
    digest = hashlib.md5(text.encode()).hexdigest()[:12]
    if prefix:
        return f"{prefix}{digest}.mp3"
    return f"{digest}.mp3"
