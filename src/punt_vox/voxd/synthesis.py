"""Synthesis pipeline -- TTS provider orchestration, caching, and direct play."""
# pyright: reportPrivateUsage=false
# Internal module within the voxd package -- cross-module private access is expected.

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Callable
from typing import Self

from punt_vox import usage
from punt_vox.normalize import VIBE_TAG_RE, normalize_for_speech
from punt_vox.providers import get_provider
from punt_vox.types import (
    AudioProviderId,
    AudioRequest,
    TTSProvider,
)
from punt_vox.types_synthesis import SynthesisSpec
from punt_vox.voxd.api_key_env import ApiKeyEnv
from punt_vox.voxd.direct_play import (
    DirectPlayFailure,
    DirectPlayInvocation,
    DirectPlayResult,
)
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
    def _build_audio_request(normalized_text: str, spec: SynthesisSpec) -> AudioRequest:
        """Build the provider's AudioRequest: *spec*'s fields over normalized text."""
        provider_id = spec.provider or ""
        return AudioRequest(
            text=normalized_text,
            voice=spec.voice,
            language=spec.language,
            rate=spec.rate,
            stability=spec.stability,
            similarity=spec.similarity,
            style=spec.style,
            speaker_boost=spec.speaker_boost,
            provider=AudioProviderId(provider_id)
            if provider_id in AudioProviderId.__members__
            else None,
        )

    @staticmethod
    def apply_vibe_for_synthesis(
        raw_text: str,
        vibe_tags: str | None,
        provider_name: str,
        model: str | None,
    ) -> str:
        """Compose the final synthesis text from raw input + vibe + capability.

        Takes the user's RAW ``raw_text`` (NOT yet normalized). The order of
        operations matters because :func:`punt_vox.normalize.normalize_for_speech`
        discards brackets via its non-prosody-punctuation filter. If we let
        normalization run first, ``[serious] hello`` becomes ``serious hello``
        and the literal word survives into TTS input.

        Non-expressive path:
            Strip ALL vibe tags (any position) via ``VIBE_TAG_RE.sub(...)``,
            collapse/trim whitespace, then normalize the cleaned text. If the
            input contains only tags, return the empty string.

        Expressive path:
            Split text into tag / non-tag segments via ``VIBE_TAG_RE``,
            normalize only the non-tag segments, reassemble with tags intact,
            then prepend session ``vibe_tags``.
        """
        expressive = SynthesisPipeline.model_supports_expressive_tags(
            provider_name, model
        )

        if not expressive:
            # Remove tags at ANY position via the regex directly -- bypass
            # strip_vibe_tags's guard (which returns original text when
            # stripping leaves nothing). For tags-only input like "[serious]"
            # the correct result is empty, not the bare word "serious".
            cleaned = VIBE_TAG_RE.sub("", raw_text)
            cleaned = re.sub(r"  +", " ", cleaned).strip()
            return normalize_for_speech(cleaned) if cleaned else ""

        # Expressive: normalize around tags so they survive at any position.
        # VIBE_TAG_RE has one capturing group, so split() interleaves:
        #   even indices -> plain text, odd indices -> tag word (no brackets).
        segments = VIBE_TAG_RE.split(raw_text)
        rebuilt: list[str] = []
        for i, seg in enumerate(segments):
            if i % 2 == 0:
                # Plain text segment -- normalize it.
                normed = normalize_for_speech(seg)
                if normed:
                    rebuilt.append(normed)
            else:
                # Captured tag word -- restore brackets.
                rebuilt.append(f"[{seg}]")

        body = " ".join(rebuilt)

        parts: list[str] = []
        if vibe_tags:
            parts.append(vibe_tags.strip())
        if body:
            parts.append(body)
        return " ".join(parts)

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
        model = spec.model

        # Normalize around bracket tags so expressive instructions survive.
        normalized = self.apply_vibe_for_synthesis(
            text, spec.vibe_tags, provider_name, model
        )

        # Anonymous cache only; credentials are deliberately excluded from identity.
        lookup = SynthesisCache.for_request(normalized, spec, len(text))
        if (cached := lookup.outcome()) is not None:
            with usage.span("daemon", provider_name, text=text, cached=True):
                return cached

        # Serialize env mutation + synthesis to avoid concurrent os.environ races.
        async with self._env_lock:
            cached = lookup.outcome()
            with usage.span(
                "daemon", provider_name, text=text, cached=cached is not None
            ):
                if cached is not None:
                    return cached
                with ApiKeyEnv(provider_name, spec.api_key).applied():
                    provider = get_provider(provider_name, model=model)
                    request = self._build_audio_request(normalized, spec)
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
        record_result: Callable[..., None],
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
        model = spec.model

        # apply_vibe_for_synthesis takes RAW text and runs normalize_for_speech
        # on the body itself (after splitting leading bracket tags off, which
        # would otherwise be eaten by normalization).
        normalized = self.apply_vibe_for_synthesis(
            text, spec.vibe_tags, provider_name, model
        )

        def _factory() -> TTSProvider:
            return get_provider(provider_name, model=model)

        start = time.monotonic()
        try:
            with usage.span("daemon", provider_name, text=text, cached=False):
                rc = usage.direct_result(
                    await self._run_direct_play(
                        spec, _factory, self._build_audio_request(normalized, spec)
                    )
                )
        except Exception as exc:
            if DirectPlayFailure(exc).unexpected:
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
