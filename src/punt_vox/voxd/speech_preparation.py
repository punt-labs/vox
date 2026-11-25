"""Speech preparation -- turn request text into a provider-ready audio request."""

from __future__ import annotations

import functools
from typing import Final

from punt_vox.normalize import SpeechText
from punt_vox.providers.caller_errors import CALLER_ERRORS
from punt_vox.types import AudioProviderId, AudioRequest, SynthesisSpec

__all__ = ["SYNTHESIS_REJECTIONS", "EmptyNormalizedTextError", "SpeechPreparation"]

# Recent texts whose normalized speech is reused; each is one request frame,
# which the server caps at 256 KiB.
_SPEECH_BODY_CACHE_SIZE: Final = 16


class EmptyNormalizedTextError(ValueError):
    """The requested model leaves no text to synthesize after normalization."""


# Errors that reject the caller's request rather than signal a daemon fault.
SYNTHESIS_REJECTIONS: Final[tuple[type[ValueError], ...]] = (
    *CALLER_ERRORS,
    EmptyNormalizedTextError,
)


class SpeechPreparation:
    """Normalize request text and gate vibe tags for the requested model."""

    __slots__ = ()

    @staticmethod
    def model_supports_expressive_tags(provider_name: str, model: str | None) -> bool:
        """Whether the given provider+model combo interprets bracket-style tags.

        Pure lookup: does NOT construct the provider or touch any SDK client,
        so it can run before voxd enters the env-mutation lock that the real
        synthesize path needs. ElevenLabs is the only provider whose answer
        depends on the model -- all others return False unconditionally.

        The ``ElevenLabsProvider`` import is deferred inside the method so
        voxd does not eagerly load the ElevenLabs SDK at module import time
        on systems whose users only ever run espeak/say. Mirrors the lazy
        pattern in :mod:`punt_vox.providers`.
        """
        if provider_name == "elevenlabs":
            from punt_vox.providers.elevenlabs import ElevenLabsProvider

            return ElevenLabsProvider.model_supports_expressive_tags(model)
        return False

    @staticmethod
    def audio_request(text: str, spec: SynthesisSpec) -> AudioRequest:
        """Normalize and validate text before preparing a provider request."""
        body, vibe_tags = SpeechPreparation._normalize(text, spec)
        if not body:
            raise EmptyNormalizedTextError("empty text after normalization")
        provider_id = spec.provider or ""
        return AudioRequest(
            text=body,
            voice=spec.voice,
            language=spec.language,
            rate=spec.rate,
            stability=spec.stability,
            similarity=spec.similarity,
            style=spec.style,
            speaker_boost=spec.speaker_boost,
            provider=AudioProviderId.__members__.get(provider_id),
            vibe_tags=vibe_tags,
        )

    @staticmethod
    def _normalize(
        raw_text: str, spec: SynthesisSpec
    ) -> tuple[str, str | None]:  # Cue is None when blank, unset, or unsupported.
        expressive = SpeechPreparation.model_supports_expressive_tags(
            spec.provider or "", spec.model
        )
        body = SpeechPreparation._speech_body(raw_text, expressive=expressive)
        vibe_tags = spec.vibe_tags.strip() if expressive and spec.vibe_tags else ""
        return body, vibe_tags or None

    @staticmethod
    @functools.lru_cache(maxsize=_SPEECH_BODY_CACHE_SIZE)
    def _speech_body(raw_text: str, *, expressive: bool) -> str:
        """Normalize text once while it stays recent.

        Normalization is CPU-bound and runs on the event loop before the first
        cache read, so a burst of hits for one long text would otherwise stall
        every other request for the full pass each time.
        """
        return SpeechText.for_synthesis(raw_text, expressive=expressive)
