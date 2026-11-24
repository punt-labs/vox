"""Synthesis pipeline -- TTS provider orchestration, caching, and direct play."""
# pyright: reportPrivateUsage=false
# Internal module within the voxd package -- cross-module private access is expected.

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Final, Self

from punt_vox import usage
from punt_vox.normalize import SpeechText
from punt_vox.providers import get_provider
from punt_vox.providers.caller_errors import CALLER_ERRORS
from punt_vox.types import (
    AudioProviderId,
    AudioRequest,
    SynthesisSpec,
    TTSProvider,
)
from punt_vox.voxd.api_key_env import ApiKeyEnv
from punt_vox.voxd.direct_play import DirectPlayInvocation, DirectPlayResult
from punt_vox.voxd.synthesis_cache import SynthesisCache
from punt_vox.voxd.synthesis_result import SynthesisOutcome
from punt_vox.voxd.temp_synthesis import TempFileSynthesis

__all__ = [
    "_LOCAL_PROVIDERS",
    "SynthesisPipeline",
]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Providers that synthesize audio directly to the default device. Cloud
# providers are skipped entirely so we don't pay for provider construction
# only to discover they don't implement play_directly.
_LOCAL_PROVIDERS: frozenset[str] = frozenset({"espeak", "say"})


class EmptyNormalizedTextError(ValueError):
    """The requested model leaves no text to synthesize after normalization."""


_SYNTHESIS_REJECTIONS: Final[tuple[type[ValueError], ...]] = (
    *CALLER_ERRORS,
    EmptyNormalizedTextError,
)


# ---------------------------------------------------------------------------
# SynthesisPipeline
# ---------------------------------------------------------------------------


class SynthesisPipeline:
    """Coordinate TTS synthesis, caching, and direct-play dispatch."""

    __slots__ = ("_env_lock", "_playback_mutex")

    _env_lock: asyncio.Lock
    _playback_mutex: asyncio.Lock

    def __new__(cls, *, playback_mutex: asyncio.Lock) -> Self:
        """Create a synthesis pipeline."""
        self = super().__new__(cls)
        self._env_lock = asyncio.Lock()
        self._playback_mutex = playback_mutex
        return self

    @property
    def env_lock(self) -> asyncio.Lock:
        """Return the lock held while a caller's API key is in the environment."""
        return self._env_lock

    # -- Static helpers ----------------------------------------------------

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
    def _prepare_audio_request(text: str, spec: SynthesisSpec) -> AudioRequest:
        """Normalize and validate text before preparing a provider request."""
        provider_id = spec.provider or ""
        return AudioRequest(
            text=SynthesisPipeline.normalized_text(text, spec),
            voice=spec.voice,
            language=spec.language,
            rate=spec.rate,
            stability=spec.stability,
            similarity=spec.similarity,
            style=spec.style,
            speaker_boost=spec.speaker_boost,
            provider=AudioProviderId.__members__.get(provider_id),
        )

    @staticmethod
    def apply_vibe_for_synthesis(
        raw_text: str,
        vibe_tags: str | None,
        provider_name: str,
        model: str | None,
    ) -> str:
        """Normalize raw input and apply vibe tags according to model capability."""
        expressive = SynthesisPipeline.model_supports_expressive_tags(
            provider_name, model
        )
        body = SpeechText.for_synthesis(raw_text, expressive=expressive)
        if expressive and vibe_tags and body:
            return " ".join(filter(None, (vibe_tags.strip(), body)))
        return body

    @staticmethod
    def normalized_text(text: str, spec: SynthesisSpec) -> str:
        """Return the normalized text, rejecting input that leaves none."""
        normalized = SynthesisPipeline.apply_vibe_for_synthesis(
            text, spec.vibe_tags, spec.provider or "", spec.model
        )
        if not normalized:
            raise EmptyNormalizedTextError("empty text after normalization")
        return normalized

    # -- Instance methods --------------------------------------------------

    async def synthesize_to_file(
        self,
        text: str,
        spec: SynthesisSpec,
    ) -> SynthesisOutcome:
        """Synthesize to a file, raising on failure; report cache reuse separately.

        Handles API key injection, provider construction, and caching. Per-call
        credentials bypass lookup and store for billing isolation.
        """
        provider_name = spec.provider or ""
        request = self._prepare_audio_request(text, spec)

        # Anonymous cache only; credentials are deliberately excluded from identity.
        lookup = SynthesisCache.for_request(request.text, spec, len(text))
        if (cached := await lookup.outcome()) is not None:
            with usage.span("daemon", provider_name, text=text, cached=True):
                return cached

        # Serialize env mutation + synthesis to avoid concurrent os.environ races.
        async with self._env_lock:
            cached = await lookup.outcome()
            with usage.span(
                "daemon", provider_name, text=text, cached=cached is not None
            ):
                if cached is not None:
                    return cached
                with ApiKeyEnv(provider_name, spec.api_key).applied():
                    provider = get_provider(provider_name, model=spec.model)
                    output_path = await TempFileSynthesis(
                        provider, request, len(text)
                    ).run()

                    # Report caching only when the anonymous write succeeds.
                    cached_path = lookup.store(output_path)
                    logger.info(
                        "synthesized %d chars%s",
                        len(text),
                        ", cached" if cached_path else "",
                    )
                    return SynthesisOutcome(path=output_path, cached=False)

    async def try_direct_play(
        self,
        text: str,
        spec: SynthesisSpec,
        *,
        record_result: Callable[..., object],
    ) -> int | Exception | None:
        """Attempt direct-to-device playback via the provider.

        Returns one of:
          * an ``int`` exit code (0 on success) when ``play_directly`` ran,
          * ``None`` when the provider opts out of direct play, or
          * an ``Exception`` instance when provider construction or playback
            raised. The caller is responsible for translating the exception
            into a websocket error response.

        The ``_env_lock`` is only acquired when an API key needs to be
        injected. Local providers (espeak, say) take a fast path with no
        cross-request blocking. Audio playback never holds the lock.
        """
        provider_name = spec.provider or ""

        def _factory() -> TTSProvider:
            return get_provider(provider_name, model=spec.model)

        start = time.monotonic()
        try:
            request = self._prepare_audio_request(text, spec)
            with usage.span("daemon", provider_name, text=text, cached=False):
                rc = usage.direct_result(
                    await self._run_direct_play(spec, _factory, request)
                )
        except Exception as exc:
            if not isinstance(exc, _SYNTHESIS_REJECTIONS):
                logger.exception("Direct-play raised")
            return exc

        if rc is None:
            return None

        DirectPlayResult(
            provider=provider_name,
            chars=len(text),
            elapsed=time.monotonic() - start,
            rc=rc,
        ).publish(record_result)
        return rc

    async def _run_direct_play(
        self,
        spec: SynthesisSpec,
        factory: Callable[[], TTSProvider],
        request: AudioRequest,
    ) -> int | None:
        """Run ``play_directly`` under the playback mutex.

        The ``_env_lock`` is added only when an API key must be injected into the
        environment; local providers take the fast, single-mutex path. The
        ``_playback_mutex`` serializes audible output across every path -- the
        queue consumer holds it too -- so two hooks firing at once cannot overlap
        even though direct play bypasses the queue.
        """
        provider_name = spec.provider or ""
        key_env = ApiKeyEnv(provider_name, spec.api_key)
        invocation = DirectPlayInvocation(provider_name, key_env, factory, request)
        if key_env.injects:
            async with self._env_lock, self._playback_mutex:
                return await asyncio.to_thread(invocation.run)
        async with self._playback_mutex:
            return await asyncio.to_thread(invocation.run)
