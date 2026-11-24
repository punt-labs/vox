"""ElevenLabs TTS provider."""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self

from punt_vox.normalize import strip_vibe_tags
from punt_vox.output import OutputResolver
from punt_vox.providers.chunked import chunked_synthesize
from punt_vox.providers.elevenlabs_catalog import ElevenLabsCatalog
from punt_vox.providers.elevenlabs_client import ElevenLabsClient, VoiceSettings
from punt_vox.types import (
    AudioProviderId,
    HealthCheck,
    SynthesisRequest,
    SynthesisResult,
    TTSProvider,
)

if TYPE_CHECKING:
    from punt_vox.providers.elevenlabs_client import ElevenLabs

logger = logging.getLogger(__name__)

__all__ = ["ElevenLabsProvider"]

# Default model. State (or an explicit ``model=`` kwarg) is the sole
# authority; the earlier ``TTS_MODEL`` env-var probe was removed to
# close the state-vs-env substitution one field over from the
# provider case (design §3.9). eleven_v3 is the highest-quality
# multilingual model and the only one that interprets bracket-style
# expressive tags ([whisper], [serious]) natively; other models
# strip the tags before synthesis. Users wanting a different model
# set it in ``vox.md``.
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


class ElevenLabsProvider(TTSProvider):
    """ElevenLabs TTS provider.

    Implements the TTSProvider protocol using the ElevenLabs SDK.
    Defaults to eleven_v3 for best voice quality. eleven_v3 interprets
    bracket-style expressive tags natively, so tags are preserved for
    that model. Other models strip tags before synthesis. A different
    model comes from ``vox.md`` (or an explicit ``model=`` kwarg),
    never from the environment: the ``TTS_MODEL`` env-var probe was
    the same substitution defect the provider gate closes, one field
    over (design §3.9).
    """

    _model: str
    _client: ElevenLabsClient
    _voices: ElevenLabsCatalog

    def __new__(
        cls,
        *,
        model: str | None = None,
        # The test seam: absent, the real SDK client is built on first use.
        client: ElevenLabs | None = None,
    ) -> Self:
        """Create an eleven labs provider."""
        self = super().__new__(cls)
        self._model = model or _DEFAULT_MODEL
        self._client = ElevenLabsClient(sdk=client)
        self._voices = ElevenLabsCatalog(self._client)
        return self

    @property
    def name(self) -> str:
        """Return the provider identifier."""
        return "elevenlabs"

    @property
    def default_voice(self) -> str:
        """Return the provider's default voice name."""
        return self._voices.default_name

    @property
    def _char_limit(self) -> int:
        return _MODEL_CHAR_LIMITS.get(self._model, _DEFAULT_CHAR_LIMIT)

    # Models that interpret bracket-style expressive tags natively.
    # eleven_v3 treats bracket tags like [alert] and [serious] as
    # expressive cues — it does not speak them as literal words.
    # Note: the ElevenLabs Model API's `can_use_style` refers to the
    # style voice-settings slider (a float 0-1), not bracket tags.
    # eleven_v3 reports can_use_style=False yet interprets bracket
    # tags. No API property exists for bracket-tag support.
    _EXPRESSIVE_MODELS: frozenset[str] = frozenset({"eleven_v3"})

    @property
    def supports_expressive_tags(self) -> bool:
        """Return whether the selected model supports expressive tags."""
        return self._model in self._EXPRESSIVE_MODELS

    @classmethod
    def model_supports_expressive_tags(cls, model: str | None) -> bool:
        """Return whether the given model interprets expressive tags natively.

        Resolves the model the same way the constructor does (explicit
        param, else :data:`_DEFAULT_MODEL`) and checks membership in
        the expressive set. Returns True for ``eleven_v3``, which
        treats bracket tags like ``[alert]`` as expressive cues. Pure:
        does not construct the provider or touch the ElevenLabs SDK.
        The ``TTS_MODEL`` env-var branch was removed in the same
        change as the constructor's, for the same reason (design §3.9).
        """
        effective = model or _DEFAULT_MODEL
        return effective in cls._EXPRESSIVE_MODELS

    def generate_audio(self, request: SynthesisRequest) -> SynthesisResult:
        """Synthesize the request into its resolved output path."""
        output_path = OutputResolver.resolve(request)
        return self.synthesize(request, output_path)

    def synthesize(
        self, request: SynthesisRequest, output_path: Path
    ) -> SynthesisResult:
        """Synthesize text to an MP3 file using ElevenLabs."""
        # Strip expressive tags for models that don't interpret them,
        # so the TTS engine doesn't speak "[serious]" as a literal word.
        text = request.text
        if not self.supports_expressive_tags:
            text = strip_vibe_tags(text)

        display_voice, voice_id = self._voices.resolve(
            request.voice or self.default_voice
        )
        char_limit = self._char_limit

        if len(text) > char_limit:
            chunked_synthesize(
                text=text,
                char_limit=char_limit,
                synthesize_chunk=lambda chunk, path: self._single_synthesize(
                    replace(request, text=chunk), path, voice_id
                ),
                output_path=output_path,
            )
        else:
            self._single_synthesize(replace(request, text=text), output_path, voice_id)

        logger.info("Wrote %s", output_path)
        return SynthesisResult(
            path=output_path,
            text=text,
            provider=AudioProviderId.elevenlabs,
            voice=display_voice,
            language=request.language,
            metadata=request.metadata,
        )

    def resolve_voice(self, name: str, language: str | None = None) -> str:  # noqa: ARG002 -- protocol requires language param
        """Validate and resolve a voice name to its canonical form.

        Language is accepted but not validated — ElevenLabs voices are
        multilingual (70+ languages with eleven_v3).
        """
        canonical, _ = self._voices.resolve(name)
        return canonical

    def check_health(self) -> list[HealthCheck]:
        """Check the ElevenLabs API key, then the account's subscription."""
        key = self._client.key_check()
        if not key.passed:
            return [key]
        return [key, self._client.subscription_check()]

    def get_default_voice(self, language: str) -> str:  # noqa: ARG002 -- protocol requires language param
        """Get the default ElevenLabs voice for a language.

        ElevenLabs voices are multilingual; always returns 'matilda'.
        """
        return self.default_voice

    def list_voices(self, language: str | None = None) -> list[str]:  # noqa: ARG002 -- protocol requires language param
        """List available voices.

        ElevenLabs voices are multilingual; language filter is accepted
        but all voices are returned regardless. Only short names (without
        descriptions) are included.
        """
        return self._voices.short_names()

    def infer_language_from_voice(self, voice: str) -> str | None:  # noqa: ARG002 -- protocol requires voice param
        """Infer language from a voice name.

        ElevenLabs voices are multilingual; always returns None.
        """
        return None

    # -- Private helpers --------------------------------------------------

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

        return VoiceSettings(  # pyright: ignore[reportUnknownVariableType, reportCallIssue]
            **kwargs,
        )

    def _single_synthesize(
        self,
        request: SynthesisRequest,
        output_path: Path,
        voice_id: str,
    ) -> None:
        """Synthesize a single chunk to a file.

        :class:`ElevenLabsClient` translates what the SDK raises: a 401 or 403
        becomes the :class:`ProviderAuthError` the wire sends the caller verbatim, a
        transport failure one of the errors ``TransportFaults`` answers. Other
        statuses (429, 500, 400) are upstream faults: a ``ProviderStatusError``,
        which the wire keeps on the opaque fault path.
        """
        voice_settings = self._build_voice_settings(request)

        kwargs: dict[str, Any] = {
            "voice_id": voice_id,
            "text": request.text,
            "model_id": self._model,
            "output_format": "mp3_44100_128",
        }
        if voice_settings is not None:
            kwargs["voice_settings"] = voice_settings

        self._client.stream_to(
            output_path, lambda sdk: sdk.text_to_speech.stream(**kwargs)
        )

        logger.info(
            "API call: provider=elevenlabs, voice=%s, chars=%d",
            voice_id,
            len(request.text),
        )
