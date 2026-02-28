"""OpenAI TTS provider."""

from __future__ import annotations

import logging
import os
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import openai

from punt_tts.core import split_text
from punt_tts.output import resolve_output_path
from punt_tts.types import (
    AudioProviderId,
    HealthCheck,
    SynthesisRequest,
    SynthesisResult,
    VoiceNotFoundError,
)

logger = logging.getLogger(__name__)

__all__ = ["OpenAIProvider"]

# Maximum characters per OpenAI TTS API request.
_MAX_CHARS = 4096

# Static voice list for tts-1 / tts-1-hd models.
VOICES: dict[str, str] = {
    "alloy": "alloy",
    "ash": "ash",
    "coral": "coral",
    "echo": "echo",
    "fable": "fable",
    "onyx": "onyx",
    "nova": "nova",
    "sage": "sage",
    "shimmer": "shimmer",
}


class OpenAIProvider:
    """OpenAI TTS provider.

    Implements the TTSProvider protocol using the OpenAI audio API.
    Supports tts-1 and tts-1-hd models with 9 built-in voices.
    """

    def __init__(
        self,
        *,
        model: str | None = None,
        client: openai.OpenAI | None = None,
    ) -> None:
        self._model = model or os.environ.get("TTS_MODEL") or "tts-1"
        self._client = client or openai.OpenAI()

    @property
    def name(self) -> str:
        return "openai"

    @property
    def default_voice(self) -> str:
        return "nova"

    @property
    def supports_expressive_tags(self) -> bool:
        return False

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
        """Synthesize text to an MP3 file using OpenAI TTS."""
        resolved_voice = request.voice or self.default_voice
        voice = self._resolve_voice_name(resolved_voice)
        rate = request.rate if request.rate is not None else 90
        speed = self._rate_to_speed(rate)

        if len(request.text) > _MAX_CHARS:
            self._chunked_synthesize(request, output_path, voice, speed)
        else:
            self._single_synthesize(request.text, output_path, voice, speed)

        logger.info("Wrote %s", output_path)
        return SynthesisResult(
            path=output_path,
            text=request.text,
            provider=AudioProviderId.openai,
            voice=voice,
            language=request.language,
            metadata=request.metadata,
        )

    def resolve_voice(self, name: str, language: str | None = None) -> str:
        """Validate and resolve a voice name to its canonical form.

        Language is accepted but not validated — OpenAI voices are multilingual.
        """
        return self._resolve_voice_name(name)

    def check_health(self) -> list[HealthCheck]:
        """Check OpenAI API key and model access."""
        checks: list[HealthCheck] = []

        # Check OPENAI_API_KEY env var.
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            checks.append(
                HealthCheck(
                    passed=False,
                    message="OpenAI API key: not set (export OPENAI_API_KEY)",
                )
            )
            return checks

        checks.append(HealthCheck(passed=True, message="OpenAI API key: set"))

        # Verify model access.
        try:
            self._client.models.retrieve(self._model)
            checks.append(
                HealthCheck(
                    passed=True,
                    message=f"OpenAI model access: {self._model}",
                )
            )
        except openai.AuthenticationError:
            checks.append(
                HealthCheck(
                    passed=False,
                    message="OpenAI model access: invalid API key",
                )
            )
        except openai.NotFoundError:
            checks.append(
                HealthCheck(
                    passed=False,
                    message=f"OpenAI model access: model '{self._model}' not found",
                )
            )
        except openai.APIConnectionError:
            checks.append(
                HealthCheck(
                    passed=False,
                    message="OpenAI model access: cannot reach API (check network)",
                )
            )

        return checks

    def get_default_voice(self, language: str) -> str:
        """Get the default OpenAI voice for a language.

        OpenAI voices are multilingual; always returns 'nova'.
        """
        return self.default_voice

    def list_voices(self, language: str | None = None) -> list[str]:
        """List available voices.

        OpenAI voices are multilingual; language filter is accepted but
        all voices are returned regardless.
        """
        return sorted(VOICES)

    def infer_language_from_voice(self, voice: str) -> str | None:
        """Infer language from a voice name.

        OpenAI voices are multilingual; always returns None.
        """
        return None

    # -- Private helpers --------------------------------------------------

    def _resolve_voice_name(self, name: str) -> str:
        """Resolve a voice name, case-insensitive."""
        key = name.lower()
        if key in VOICES:
            return VOICES[key]
        raise VoiceNotFoundError(name, sorted(VOICES))

    @staticmethod
    def _rate_to_speed(rate: int) -> float:
        """Convert percentage rate to OpenAI speed parameter.

        rate=100 → speed=1.0, rate=90 → speed=0.9.
        Clamped to OpenAI's [0.25, 4.0] range.
        """
        return max(0.25, min(4.0, rate / 100))

    def _single_synthesize(
        self,
        text: str,
        output_path: Path,
        voice: str,
        speed: float,
    ) -> None:
        """Synthesize a single chunk to a file."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        response: Any = self._client.audio.speech.create(  # pyright: ignore[reportUnknownMemberType]
            model=self._model,
            voice=voice,  # pyright: ignore[reportArgumentType]
            input=text,
            speed=speed,
            response_format="mp3",
        )
        logger.info(
            "API call: provider=openai, voice=%s, chars=%d",
            voice,
            len(text),
        )
        output_path.write_bytes(response.content)  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]

    def _chunked_synthesize(
        self,
        request: SynthesisRequest,
        output_path: Path,
        voice: str,
        speed: float,
    ) -> None:
        """Split text into chunks, synthesize each, then stitch."""
        from punt_tts.core import stitch_audio

        chunks = split_text(request.text, _MAX_CHARS)
        logger.debug("Chunked %d chars into %d parts", len(request.text), len(chunks))

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            paths: list[Path] = []

            for i, chunk in enumerate(chunks):
                chunk_path = tmp_dir / f"chunk_{i:04d}.mp3"
                self._single_synthesize(chunk, chunk_path, voice, speed)
                paths.append(chunk_path)

            stitch_audio(paths, output_path, pause_ms=0)
