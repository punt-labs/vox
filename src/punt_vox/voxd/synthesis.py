"""Synthesis pipeline -- TTS provider orchestration, caching, and direct play."""
# pyright: reportPrivateUsage=false
# Internal module within the voxd package -- cross-module private access is expected.

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import tempfile
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Self

from punt_vox.cache import cache_get, cache_put
from punt_vox.core import TTSClient
from punt_vox.normalize import VIBE_TAG_RE, normalize_for_speech
from punt_vox.providers import get_provider
from punt_vox.types import (
    AudioProviderId,
    AudioRequest,
    DirectPlayProvider,
    TTSProvider,
)
from punt_vox.types_synthesis import SynthesisSpec
from punt_vox.voxd.playback import _monotonic

__all__ = [
    "_LOCAL_PROVIDERS",
    "_PROVIDER_API_KEY_VAR",
    "SynthesisPipeline",
    "_build_audio_request",
    "_run_play_directly_sync",
]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Providers that synthesize audio directly to the default device. Cloud
# providers are skipped entirely so we don't pay for provider construction
# only to discover they don't implement play_directly.
_LOCAL_PROVIDERS: frozenset[str] = frozenset({"espeak", "say"})

# Map of provider name to its expected API key env var. Used by the
# direct-play env-injection helper.
_PROVIDER_API_KEY_VAR: dict[str, str] = {
    "elevenlabs": "ELEVENLABS_API_KEY",
    "openai": "OPENAI_API_KEY",
}


@contextlib.contextmanager
def _api_key_context(api_key: str | None, provider_name: str) -> Generator[None]:
    """Temporarily inject api_key into os.environ for the provider."""
    env_key_name = _PROVIDER_API_KEY_VAR.get(provider_name)
    old_key: str | None = None
    if api_key and env_key_name:
        old_key = os.environ.get(env_key_name)
        os.environ[env_key_name] = api_key
    try:
        yield
    finally:
        if api_key and env_key_name:
            if old_key is not None:
                os.environ[env_key_name] = old_key
            else:
                os.environ.pop(env_key_name, None)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _build_audio_request(
    normalized_text: str,
    voice: str | None,
    language: str | None,
    rate: int | None,
    stability: float | None,
    similarity: float | None,
    style: float | None,
    *,
    speaker_boost: bool | None,
    provider_id: str,
) -> AudioRequest:
    """Build an AudioRequest from parsed message fields."""
    return AudioRequest(
        text=normalized_text,
        voice=voice,
        language=language,
        rate=rate,
        stability=stability,
        similarity=similarity,
        style=style,
        speaker_boost=speaker_boost,
        provider=AudioProviderId(provider_id)
        if provider_id in AudioProviderId.__members__
        else None,
    )


def _run_play_directly_sync(
    provider_name: str,
    api_key: str | None,
    provider_factory: Callable[[], TTSProvider],
    request: AudioRequest,
) -> int | None:
    """Construct provider and call ``play_directly`` on a worker thread.

    Returns ``None`` if the provider does not implement the
    ``DirectPlayProvider`` protocol -- the caller will fall back to the
    synthesize-and-queue path. Mutates ``os.environ`` only if an API key
    is supplied; restoration happens on the same thread so the env-lock
    contract is preserved without holding the lock during audio playback.
    """
    with _api_key_context(api_key, provider_name):
        provider = provider_factory()
        if not isinstance(provider, DirectPlayProvider):
            return None
        return provider.play_directly(request)


# ---------------------------------------------------------------------------
# SynthesisPipeline
# ---------------------------------------------------------------------------


class SynthesisPipeline:
    """Coordinate TTS synthesis, caching, and direct-play dispatch."""

    __slots__ = ("_env_lock", "_playback_mutex")

    _env_lock: asyncio.Lock
    _playback_mutex: asyncio.Lock

    def __new__(cls, *, playback_mutex: asyncio.Lock) -> Self:
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
        *,
        request_id: str = "",
    ) -> Path:
        """Run TTS synthesis and return the output path.

        Handles API key injection, provider construction, and caching.
        Raises on failure.

        When ``api_key`` is set, the cache is bypassed on both the lookup
        and the store so the per-call billing scope (vox-a3e) never reads
        bytes that were synthesized under a different key and never leaves
        bytes behind that a later call on a different key could reuse. The
        anonymous path (``api_key is None``) uses the MD5-keyed on-disk
        cache unchanged. See ``src/punt_vox/cache.py`` for the rationale.
        """
        voice = spec.voice
        provider_name = spec.provider or ""
        model = spec.model
        api_key = spec.api_key
        resolved_voice = voice or ""

        # apply_vibe_for_synthesis takes RAW text and runs normalize_for_speech
        # on the body itself (after splitting leading bracket tags off, which
        # would otherwise be eaten by normalization).
        normalized = self.apply_vibe_for_synthesis(
            text, spec.vibe_tags, provider_name, model
        )

        # Cache lookup: anonymous calls only. Per-call api_key scopes
        # bypass the cache entirely so a billing-isolated call never
        # reads bytes synthesized under a different key (or no key).
        # CodeQL py/weak-sensitive-data-hashing also required that we
        # never feed the api_key into any digest in cache.py.
        if api_key is None:
            cached = cache_get(normalized, resolved_voice, provider_name)
            if cached is not None:
                return cached
        else:
            logger.debug(
                "Per-call api_key set; bypassing cache for this request (id=%s)",
                request_id,
            )

        # Serialize env mutation + synthesis to avoid concurrent os.environ races.
        async with self._env_lock:
            with _api_key_context(api_key, provider_name):
                provider = get_provider(provider_name, config_dir=None, model=model)
                request = _build_audio_request(
                    normalized,
                    voice,
                    spec.language,
                    spec.rate,
                    spec.stability,
                    spec.similarity,
                    spec.style,
                    speaker_boost=spec.speaker_boost,
                    provider_id=provider_name,
                )
                client = TTSClient(provider)

                with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
                    output_path = Path(tmp.name)

                await asyncio.to_thread(client.synthesize, request, output_path)

                try:
                    synth_size = output_path.stat().st_size
                except OSError:
                    synth_size = -1
                if synth_size <= 0:
                    logger.error(
                        "synthesize FAILED: provider=%s voice=%s file=%s "
                        "size=%d chars_in=%d -- zero-byte or missing output",
                        provider_name,
                        resolved_voice,
                        output_path,
                        synth_size,
                        len(text),
                    )
                    # Delete the broken temp file and fail fast. Caching it
                    # would poison every subsequent identical request.
                    output_path.unlink(missing_ok=True)
                    msg = (
                        f"synthesis produced missing or empty output file: "
                        f"{output_path} (provider={provider_name}, "
                        f"voice={resolved_voice}, chars_in={len(text)})"
                    )
                    raise RuntimeError(msg)

                logger.info(
                    "synthesize done: provider=%s voice=%s file=%s size=%d chars_in=%d",
                    provider_name,
                    resolved_voice,
                    output_path,
                    synth_size,
                    len(text),
                )

                # Only cache verified-good output, and only on the
                # anonymous path. Per-call api_key scopes skip cache_put
                # so a billing-isolated call can never leave bytes behind
                # that a later call on a different key could reuse.
                if api_key is None:
                    cache_put(normalized, resolved_voice, provider_name, output_path)
                return output_path

    async def try_direct_play(
        self,
        text: str,
        spec: SynthesisSpec,
        *,
        record_result: Callable[..., None],
    ) -> int | None | Exception:
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
        voice = spec.voice
        provider_name = spec.provider or ""
        model = spec.model
        api_key = spec.api_key

        # apply_vibe_for_synthesis takes RAW text and runs normalize_for_speech
        # on the body itself (after splitting leading bracket tags off, which
        # would otherwise be eaten by normalization).
        normalized = self.apply_vibe_for_synthesis(
            text, spec.vibe_tags, provider_name, model
        )

        request = _build_audio_request(
            normalized,
            voice,
            spec.language,
            spec.rate,
            spec.stability,
            spec.similarity,
            spec.style,
            speaker_boost=spec.speaker_boost,
            provider_id=provider_name,
        )

        def _factory() -> TTSProvider:
            return get_provider(provider_name, config_dir=None, model=model)

        start = _monotonic()
        try:
            # _playback_mutex serializes audible output across all paths --
            # the queue consumer holds it too. Without this, two hooks firing
            # at once would overlap because direct-play bypasses the queue.
            if api_key and provider_name in _PROVIDER_API_KEY_VAR:
                async with self._env_lock, self._playback_mutex:
                    rc = await asyncio.to_thread(
                        _run_play_directly_sync,
                        provider_name,
                        api_key,
                        _factory,
                        request,
                    )
            else:
                async with self._playback_mutex:
                    rc = await asyncio.to_thread(
                        _run_play_directly_sync,
                        provider_name,
                        None,
                        _factory,
                        request,
                    )
        except Exception as exc:
            logger.exception("Direct-play raised for provider=%s", provider_name)
            return exc

        if rc is None:
            return None

        elapsed = _monotonic() - start
        record_result(
            path=Path(f"<direct:{provider_name}>"),
            rc=rc,
            elapsed=elapsed,
            stderr="" if rc == 0 else f"play_directly rc={rc}",
        )
        if rc == 0:
            logger.info(
                "Direct-play ok: provider=%s voice=%s elapsed=%.3fs chars=%d",
                provider_name,
                voice or "",
                elapsed,
                len(text),
            )
        else:
            logger.error(
                "Direct-play FAILED: provider=%s voice=%s elapsed=%.3fs rc=%d",
                provider_name,
                voice or "",
                elapsed,
                rc,
            )
        return rc
