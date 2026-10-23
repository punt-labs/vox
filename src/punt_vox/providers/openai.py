"""OpenAI TTS provider."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Self

import openai

from punt_vox.normalize import strip_vibe_tags
from punt_vox.output import OutputResolver
from punt_vox.providers.api_key import ApiKeyCheck
from punt_vox.providers.chunked import chunked_synthesize
from punt_vox.providers.lazy_sdk import LazySdk
from punt_vox.providers.provider_faults import TRANSPORT_ERRORS
from punt_vox.providers.provider_refusal import ProviderRefusalError
from punt_vox.providers.stream_file import StreamFile
from punt_vox.types import (
    AudioProviderId,
    HealthCheck,
    SynthesisRequest,
    SynthesisResult,
    TTSProvider,
    VoiceNotFoundError,
)

logger = logging.getLogger(__name__)

__all__ = ["OpenAIProvider"]

# The variable the readiness gate names for this provider, not a second literal.
_KEY_CHECK = ApiKeyCheck.for_provider("openai")

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

    The key is read here, though. voxd swaps ``OPENAI_API_KEY`` in for the
    length of a request that carries its own key and builds the provider
    inside that window; the first SDK build can run later, on a worker thread
    that outlived a cancelled request, after the swap is undone. Reading the
    key then would send whatever key is in the environment at that moment --
    possibly another caller's.
    """

    _model: str
    # None: no key in the environment at construction; check_health says so.
    _api_key: str | None
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
        self._api_key = os.environ.get(_KEY_CHECK.source)
        self._client = LazySdk(
            self._new_sdk, transport=openai.APIConnectionError, sdk=client
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
        key = _KEY_CHECK.report("OpenAI", self._api_key)
        if not key.passed:
            return [key]
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
        if isinstance(exc, openai.PermissionDeniedError):
            reason = self._refused(exc).reason
            return f"HTTP 403: {reason}" if reason else "HTTP 403"
        return f"HTTP {exc.status_code}"

    @staticmethod
    def _refused(
        exc: openai.AuthenticationError | openai.PermissionDeniedError,
    ) -> ProviderRefusalError:
        """Return the refusal the caller sees, with the server's error code.

        A 403 is not always the key -- the project or the region can be at
        fault, and the code says which -- so its body, which never holds the
        key, is logged whole. A 401's message can quote a masked key, so only
        its code is.
        """
        if isinstance(exc, openai.PermissionDeniedError):
            logger.error(
                "openai refused the request (HTTP 403): code=%r message=%r",
                exc.code,
                exc.message,
            )
        else:
            logger.error("openai rejected the key (HTTP 401): code=%r", exc.code)
        # A response arrived, so the SDK's status_code is always set.
        return ProviderRefusalError.from_code("openai", exc.status_code, exc.code)

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

    def _new_sdk(self) -> openai.OpenAI:
        """Construct the SDK client with the key this provider was built with.

        Passed explicitly, so the SDK never reads ``OPENAI_API_KEY`` itself --
        given no key, it would -- and checked first: the SDK puts the key in an
        ``Authorization`` header, and httpx's refusal of a header it cannot
        encode quotes the key.

        Raises:
            MalformedApiKeyError: The key cannot be sent as a header value.
            ProviderUnavailableError: No key was set when this provider was
                built.
        """
        return openai.OpenAI(api_key=_KEY_CHECK.require_set(self._api_key, self.name))

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
            raise self._refused(exc) from exc
        StreamFile(output_path).write((response.content,))
        logger.info(
            "API call: provider=openai, voice=%s, chars=%d",
            voice,
            len(text),
        )
