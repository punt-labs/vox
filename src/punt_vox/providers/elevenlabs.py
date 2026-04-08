"""ElevenLabs TTS provider."""

from __future__ import annotations

import logging
import os
import re
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from elevenlabs.core import ApiError  # pyright: ignore[reportMissingTypeStubs]

from punt_vox.output import resolve_output_path
from punt_vox.types import (
    AudioProviderId,
    HealthCheck,
    SynthesisRequest,
    SynthesisResult,
    VoiceNotFoundError,
)

logger = logging.getLogger(__name__)

__all__ = ["ElevenLabsProvider"]

# Default model — eleven_v3 is the only model today that interprets
# bracket-style expressive tags ([excited], [weary], [sighs]) which the
# /vibe feature is built around. Using a non-expressive default (flash
# or turbo) silently makes the TTS engine speak the literal text "excited"
# etc instead of rendering the tag, which is worse than not having the
# feature at all. Users who want lower cost or lower latency can still
# override via TTS_MODEL=eleven_flash_v2_5.
_DEFAULT_MODEL = "eleven_v3"

# Character limits per model (from ElevenLabs docs).
_MODEL_CHAR_LIMITS: dict[str, int] = {
    "eleven_v3": 5_000,
    "eleven_flash_v2_5": 40_000,
    "eleven_turbo_v2_5": 10_000,
    "eleven_turbo_v2": 10_000,
    "eleven_multilingual_v2": 10_000,
    "eleven_monolingual_v1": 10_000,
}
_DEFAULT_CHAR_LIMIT = 10_000

# ElevenLabs voice IDs are 20-char alphanumeric strings.
_VOICE_ID_RE = re.compile(r"^[0-9a-zA-Z]{20}$")

# Cache of resolved voices, keyed by lowercase name → voice_id.
VOICES: dict[str, str] = {}

# Whether the voice list has been fetched from the API.
_voices_loaded: bool = False


def _load_voices_from_api(client: Any) -> None:  # pyright: ignore[reportExplicitAny]
    """Fetch all voices from the ElevenLabs API and populate the cache."""
    global _voices_loaded
    if _voices_loaded:
        return

    response: Any = client.voices.get_all()  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    for voice in response.voices:  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        full_name: str = voice.name.lower()  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        vid: str = voice.voice_id  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]

        # Store full name (e.g. "adam - dominant, firm").
        if full_name not in VOICES:
            VOICES[full_name] = vid

        # Also store short name (before " - ") for convenient lookup.
        short_name = full_name.split(" - ", 1)[0]
        if short_name != full_name and short_name not in VOICES:
            VOICES[short_name] = vid

    _voices_loaded = True
    logger.debug("Loaded %d voice entries from ElevenLabs API", len(VOICES))


def _extract_api_error_message(exc: ApiError) -> str:
    """Extract a human-readable message from an ElevenLabs ApiError."""
    body: Any = exc.body  # pyright: ignore[reportUnknownMemberType]
    if isinstance(body, dict):
        detail: Any = body.get("detail", {})  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        if isinstance(detail, dict):
            msg: Any = detail.get("message", "")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            if isinstance(msg, str) and msg:
                return msg
    return f"HTTP {exc.status_code}"


class ElevenLabsProvider:
    """ElevenLabs TTS provider.

    Implements the TTSProvider protocol using the ElevenLabs SDK.
    Defaults to eleven_v3 — the only model that interprets bracket-style
    expressive tags (``[excited]``, ``[weary]``, ``[sighs]``) which the
    ``/vibe`` feature is built around. Override with ``TTS_MODEL`` env
    var for lower cost or latency (e.g. ``eleven_flash_v2_5``).
    """

    def __init__(
        self,
        *,
        model: str | None = None,
        client: Any | None = None,  # pyright: ignore[reportExplicitAny]
    ) -> None:
        self._model = model or os.environ.get("TTS_MODEL") or _DEFAULT_MODEL
        if client is not None:
            self._client: Any = client  # pyright: ignore[reportExplicitAny]
        else:
            from elevenlabs import ElevenLabs  # pyright: ignore[reportMissingTypeStubs]

            self._client = ElevenLabs()  # pyright: ignore[reportUnknownMemberType]

    @property
    def name(self) -> str:
        return "elevenlabs"

    @property
    def default_voice(self) -> str:
        return "matilda"

    # Models that interpret bracket-style expressive tags (e.g. [excited]).
    _EXPRESSIVE_MODELS: frozenset[str] = frozenset({"eleven_v3"})

    @property
    def supports_expressive_tags(self) -> bool:
        return self._model in self._EXPRESSIVE_MODELS

    def generate_audio(self, request: SynthesisRequest) -> SynthesisResult:
        output_path = resolve_output_path(request)
        return self.synthesize(request, output_path)

    def generate_audios(
        self, requests: Sequence[SynthesisRequest]
    ) -> list[SynthesisResult]:
        return [self.generate_audio(request) for request in requests]

    def synthesize(
        self, request: SynthesisRequest, output_path: Path
    ) -> SynthesisResult:
        """Synthesize text to an MP3 file using ElevenLabs."""
        resolved_voice = request.voice or self.default_voice
        voice_id = self._resolve_voice_id(resolved_voice)
        rate = request.rate if request.rate is not None else 100

        if rate != 100:
            logger.debug(
                "ElevenLabs does not support rate adjustment (got rate=%d). "
                "Audio will be at normal speed.",
                rate,
            )

        char_limit = _MODEL_CHAR_LIMITS.get(self._model, _DEFAULT_CHAR_LIMIT)

        if len(request.text) > char_limit:
            self._chunked_synthesize(request, output_path, voice_id, char_limit)
        else:
            self._single_synthesize(request.text, output_path, voice_id, request)

        logger.info("Wrote %s", output_path)
        display_voice = (
            resolved_voice
            if _VOICE_ID_RE.match(resolved_voice)
            else resolved_voice.lower()
        )
        return SynthesisResult(
            path=output_path,
            text=request.text,
            provider=AudioProviderId.elevenlabs,
            voice=display_voice,
            language=request.language,
            metadata=request.metadata,
        )

    def resolve_voice(self, name: str, language: str | None = None) -> str:
        """Validate and resolve a voice name to its canonical form.

        Language is accepted but not validated — ElevenLabs voices are
        multilingual (70+ languages with eleven_v3).
        """
        self._resolve_voice_id(name)
        if _VOICE_ID_RE.match(name):
            return name
        return name.lower()

    def check_health(self) -> list[HealthCheck]:
        """Check ElevenLabs API key and subscription status."""
        checks: list[HealthCheck] = []

        api_key = os.environ.get("ELEVENLABS_API_KEY")
        if not api_key:
            checks.append(
                HealthCheck(
                    passed=False,
                    message=("ElevenLabs API key: not set (export ELEVENLABS_API_KEY)"),
                )
            )
            return checks

        checks.append(HealthCheck(passed=True, message="ElevenLabs API key: set"))

        try:
            sub: Any = self._client.user.subscription.get()  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            tier: str = sub.tier  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            used: int = sub.character_count  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            limit: int = sub.character_limit  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            checks.append(
                HealthCheck(
                    passed=True,
                    message=(
                        f"ElevenLabs subscription: {tier} ({used:,}/{limit:,} chars)"
                    ),
                )
            )
        except ApiError as exc:
            msg = _extract_api_error_message(exc)
            checks.append(
                HealthCheck(
                    passed=False,
                    message=f"ElevenLabs subscription: {msg}",
                )
            )
        except OSError as exc:
            checks.append(
                HealthCheck(
                    passed=False,
                    message=f"ElevenLabs subscription: {exc}",
                )
            )

        return checks

    def get_default_voice(self, language: str) -> str:
        """Get the default ElevenLabs voice for a language.

        ElevenLabs voices are multilingual; always returns 'matilda'.
        """
        return self.default_voice

    def list_voices(self, language: str | None = None) -> list[str]:
        """List available voices.

        ElevenLabs voices are multilingual; language filter is accepted
        but all voices are returned regardless. Only short names (without
        descriptions) are included.
        """
        _load_voices_from_api(self._client)
        return sorted(k for k in VOICES if " - " not in k)

    def infer_language_from_voice(self, voice: str) -> str | None:
        """Infer language from a voice name.

        ElevenLabs voices are multilingual; always returns None.
        """
        return None

    # -- Private helpers --------------------------------------------------

    def _resolve_voice_id(self, name: str) -> str:
        """Resolve a voice name or ID to a voice_id string."""
        # Accept raw voice_id (20 alphanumeric chars) directly.
        if _VOICE_ID_RE.match(name):
            return name

        key = name.lower()
        if key in VOICES:
            return VOICES[key]

        _load_voices_from_api(self._client)

        if key in VOICES:
            return VOICES[key]

        short_names = sorted(k for k in VOICES if " - " not in k)
        raise VoiceNotFoundError(name, short_names)

    def _build_voice_settings(self, request: SynthesisRequest) -> Any | None:  # pyright: ignore[reportExplicitAny]
        """Build VoiceSettings from request fields, or None for defaults."""
        kwargs: dict[str, Any] = {}
        if request.stability is not None:
            kwargs["stability"] = request.stability
        if request.similarity is not None:
            kwargs["similarity_boost"] = request.similarity
        if request.style is not None:
            kwargs["style"] = request.style
        if request.speaker_boost is not None:
            kwargs["use_speaker_boost"] = request.speaker_boost

        if not kwargs:
            return None

        from elevenlabs.types import (
            VoiceSettings,  # pyright: ignore[reportMissingTypeStubs]
        )

        return VoiceSettings(  # pyright: ignore[reportUnknownVariableType, reportCallIssue]
            **kwargs,
        )

    def _single_synthesize(
        self,
        text: str,
        output_path: Path,
        voice_id: str,
        request: SynthesisRequest,
    ) -> None:
        """Synthesize a single chunk to a file."""
        output_path.parent.mkdir(parents=True, exist_ok=True)

        voice_settings = self._build_voice_settings(request)

        kwargs: dict[str, Any] = {
            "voice_id": voice_id,
            "text": text,
            "model_id": self._model,
            "output_format": "mp3_44100_128",
        }
        if voice_settings is not None:
            kwargs["voice_settings"] = voice_settings

        response: Any = self._client.text_to_speech.stream(**kwargs)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]

        logger.info(
            "API call: provider=elevenlabs, voice=%s, chars=%d",
            voice_id,
            len(text),
        )
        with open(output_path, "wb") as f:
            for chunk in response:  # pyright: ignore[reportUnknownVariableType]
                f.write(chunk)  # pyright: ignore[reportUnknownArgumentType]

    def _chunked_synthesize(
        self,
        request: SynthesisRequest,
        output_path: Path,
        voice_id: str,
        char_limit: int,
    ) -> None:
        """Split text into chunks, synthesize each, then stitch."""
        from punt_vox.core import split_text, stitch_audio

        chunks = split_text(request.text, char_limit)
        logger.debug(
            "Chunked %d chars into %d parts",
            len(request.text),
            len(chunks),
        )

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            paths: list[Path] = []

            for i, chunk in enumerate(chunks):
                chunk_path = tmp_dir / f"chunk_{i:04d}.mp3"
                self._single_synthesize(chunk, chunk_path, voice_id, request)
                paths.append(chunk_path)

            stitch_audio(paths, output_path, pause_ms=0)
