"""OpenAI TTS provider."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Self

import openai

from punt_vox.normalize import strip_vibe_tags
from punt_vox.output import OutputResolver
from punt_vox.providers.chunked import chunked_synthesize
from punt_vox.providers.lazy_sdk import LazySdk
from punt_vox.providers.provider_faults import TRANSPORT_ERRORS
from punt_vox.providers.stream_file import StreamFile
from punt_vox.types import (
    AudioProviderId,
    HealthCheck,
    SynthesisRequest,
    SynthesisResult,
    TTSProvider,
    VoiceNotFoundError,
)
from punt_vox.types_provider_errors import ProviderAuthError

logger = logging.getLogger(__name__)

__all__ = ["OpenAIProvider"]

# Maximum characters per OpenAI TTS API request.
_MAX_CHARS = 4096

# Default model. State (or an explicit ``model=`` kwarg) is the sole
# authority: the ``TTS_MODEL`` env-var probe was removed to close the
# state-vs-env substitution one field over from the provider case
# (design §3.9). ``vox.md`` says which model to use; this constant is
# the fallback when state declares none.
_DEFAULT_MODEL = "tts-1"

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


class OpenAIProvider(TTSProvider):
    """OpenAI TTS provider.

    Implements the TTSProvider protocol using the OpenAI audio API.
    Supports tts-1 and tts-1-hd models with 9 built-in voices.

    The SDK client is built on first use, not here: constructing it loads
    ``SSL_CERT_FILE``, and a bad bundle should fail the request that needs
    TLS -- by name -- rather than the code that merely composes a provider.
    """

    _model: str
    _client: LazySdk[openai.OpenAI]

    def __new__(
        cls,
        *,
        model: str | None = None,
        # The test seam: absent, the real SDK client is built on first use.
        client: openai.OpenAI | None = None,
    ) -> Self:
        self = super().__new__(cls)
        self._model = model or _DEFAULT_MODEL
        self._client = LazySdk(
            openai.OpenAI, transport=openai.APIConnectionError, sdk=client
        )
        return self

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
        output_path = OutputResolver.resolve(request)
        return self.synthesize(request, output_path)

    def synthesize(
        self, request: SynthesisRequest, output_path: Path
    ) -> SynthesisResult:
        """Synthesize text to an MP3 file using OpenAI TTS."""
        text = strip_vibe_tags(request.text)

        resolved_voice = request.voice or self.default_voice
        voice = self._resolve_voice_name(resolved_voice)
        rate = request.rate if request.rate is not None else 90
        speed = self._rate_to_speed(rate)

        if len(text) > _MAX_CHARS:
            chunked_synthesize(
                text=text,
                char_limit=_MAX_CHARS,
                synthesize_chunk=lambda chunk, path: self._single_synthesize(
                    chunk, path, voice, speed
                ),
                output_path=output_path,
            )
        else:
            self._single_synthesize(text, output_path, voice, speed)

        logger.info("Wrote %s", output_path)
        return SynthesisResult(
            path=output_path,
            text=text,
            provider=AudioProviderId.openai,
            voice=voice,
            language=request.language,
            metadata=request.metadata,
        )

    def resolve_voice(self, name: str, language: str | None = None) -> str:  # noqa: ARG002 -- protocol requires language param
        """Validate and resolve a voice name to its canonical form.

        Language is accepted but not validated — OpenAI voices are multilingual.
        """
        return self._resolve_voice_name(name)

    def check_health(self) -> list[HealthCheck]:
        """Check the OpenAI API key, then the configured model's access."""
        if not os.environ.get("OPENAI_API_KEY"):
            return [
                HealthCheck(
                    passed=False,
                    message="OpenAI API key: not set (export OPENAI_API_KEY)",
                )
            ]
        key = HealthCheck(passed=True, message="OpenAI API key: set")
        return [key, self._model_access_check()]

    def _model_access_check(self) -> HealthCheck:
        """Report whether the configured model is reachable with this key."""
        try:
            self._client.call(lambda sdk: sdk.models.retrieve(self._model))
        except openai.APIStatusError as exc:
            reason = self._refusal(exc)
        except TRANSPORT_ERRORS as exc:
            reason = str(exc)
        else:
            return HealthCheck(
                passed=True, message=f"OpenAI model access: {self._model}"
            )
        return HealthCheck(passed=False, message=f"OpenAI model access: {reason}")

    def _refusal(self, exc: openai.APIStatusError) -> str:
        """Say why the server refused the model lookup -- any status, never raised."""
        if isinstance(exc, openai.AuthenticationError):
            return "invalid API key"
        if isinstance(exc, openai.NotFoundError):
            return f"model '{self._model}' not found"
        return f"HTTP {exc.status_code}"

    def get_default_voice(self, language: str) -> str:  # noqa: ARG002 -- protocol requires language param
        """Get the default OpenAI voice for a language.

        OpenAI voices are multilingual; always returns 'nova'.
        """
        return self.default_voice

    def list_voices(self, language: str | None = None) -> list[str]:  # noqa: ARG002 -- protocol requires language param
        """List available voices.

        OpenAI voices are multilingual; language filter is accepted but
        all voices are returned regardless.
        """
        return sorted(VOICES)

    def infer_language_from_voice(self, voice: str) -> str | None:  # noqa: ARG002 -- protocol requires voice param
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
        """Synthesize a single chunk to a file, all of it or nothing.

        A rejected credential at synthesis time -- 401 for a revoked or wrong
        key, 403 for a key whose project or org has no TTS access -- becomes a
        :class:`ProviderAuthError` that ``WireReply.reject_or_fault`` routes
        verbatim to the client, rather than falling through to speech_handlers'
        broad guard and getting laundered to ``"operation failed"``. The
        readiness gate (:class:`ProviderCredentials`) catches the
        credential-absent case at F2; this catches the
        credential-present-but-rejected case at F3.

        The audio lands through :class:`StreamFile`: renamed into place only
        once whole, and an empty body is rejected rather than cached.
        """
        try:
            response = self._client.call(
                lambda sdk: sdk.audio.speech.create(
                    model=self._model,
                    voice=voice,  # pyright: ignore[reportArgumentType]
                    input=text,
                    speed=speed,
                    response_format="mp3",
                )
            )
        except (openai.AuthenticationError, openai.PermissionDeniedError) as exc:
            # A response arrived, so the SDK's status_code is always set.
            raise ProviderAuthError("openai", exc.status_code) from exc
        StreamFile(output_path).write((response.content,))
        logger.info(
            "API call: provider=openai, voice=%s, chars=%d",
            voice,
            len(text),
        )
