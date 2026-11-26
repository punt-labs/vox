"""Synthesis pipeline -- TTS provider orchestration, caching, and direct play."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from contextlib import ExitStack, nullcontext
from typing import Self

from punt_vox import usage
from punt_vox.providers import get_provider
from punt_vox.types import SynthesisSpec, TTSProvider
from punt_vox.voxd.api_key_env import ApiKeyEnv
from punt_vox.voxd.direct_play import DirectPlayInvocation, DirectPlayResult
from punt_vox.voxd.speech_preparation import SYNTHESIS_REJECTIONS, SpeechPreparation
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

    @property
    def env_lock(self) -> asyncio.Lock:
        """Return the lock held while a caller's API key is in the environment."""
        return self._env_lock

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
        request = SpeechPreparation.audio_request(text, spec)

        # Anonymous cache only; credentials are deliberately excluded from identity.
        lookup = SynthesisCache.for_request(
            request.text, spec, len(text), vibe_tags=request.vibe_tags
        )
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
                    temp = TempFileSynthesis(provider, request, len(text))
                    outcome = SynthesisOutcome(path=await temp.run(), cached=False)
                    with ExitStack() as unclaimed:
                        unclaimed.callback(outcome.discard)
                        suffix = ", cached" if lookup.store(outcome.path) else ""
                        logger.info("synthesized %d chars%s", len(text), suffix)
                        unclaimed.pop_all()
                    return outcome

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
            request = SpeechPreparation.audio_request(text, spec)
            with usage.span("daemon", provider_name, text=text, cached=False):
                invocation = DirectPlayInvocation(
                    provider_name,
                    ApiKeyEnv(provider_name, spec.api_key),
                    _factory,
                    request,
                )
                rc = usage.direct_result(await self._run_direct_play(invocation))
        except Exception as exc:
            if not isinstance(exc, SYNTHESIS_REJECTIONS):
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

    async def _run_direct_play(self, invocation: DirectPlayInvocation) -> int | None:
        """Run ``play_directly`` under the playback mutex.

        The ``_env_lock`` is added only when an API key must be injected into the
        environment; local providers take the fast, single-mutex path. The
        ``_playback_mutex`` serializes audible output across every path -- the
        queue consumer holds it too -- so two hooks firing at once cannot overlap
        even though direct play bypasses the queue.
        """
        env_guard = self._env_lock if invocation.key_env.injects else nullcontext()
        async with env_guard, self._playback_mutex:
            return await asyncio.to_thread(invocation.run)
