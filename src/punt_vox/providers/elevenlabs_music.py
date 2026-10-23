"""ElevenLabs Music generation provider."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import TYPE_CHECKING, Self

from elevenlabs.core import ApiError  # pyright: ignore[reportMissingTypeStubs]

from punt_vox.providers.elevenlabs_client import ElevenLabsClient
from punt_vox.types import MusicProvider

if TYPE_CHECKING:
    from pathlib import Path

    from elevenlabs.client import ElevenLabs

logger = logging.getLogger(__name__)

__all__ = ["ElevenLabsMusicProvider"]

_DEFAULT_OUTPUT_FORMAT = "mp3_44100_128"


class ElevenLabsMusicProvider(MusicProvider):
    """ElevenLabs Music provider.

    Implements the MusicProvider protocol using the ElevenLabs SDK's
    music.stream endpoint. Streams bytes to a temp file in the same
    directory as the target, then renames atomically on completion.

    Always passes force_instrumental=True — background music should
    never have vocals.

    The SDK client is built on the first generation, not here: the daemon
    constructs this provider while composing its subsystems, and a client
    built then would load ``SSL_CERT_FILE`` at boot, so a bad CA path would
    stop voxd from speaking at all instead of failing only the music.
    """

    __slots__ = ("_client", "_output_format")
    _output_format: str
    _client: ElevenLabsClient

    def __new__(
        cls,
        *,
        # Absent: read ELEVENLABS_API_KEY now -- see ElevenLabsClient for why the
        # key is fixed at construction rather than at the first generation.
        api_key: str | None = None,
        # The test seam: absent, the real SDK client is built on first use.
        client: ElevenLabs | None = None,
        output_format: str = _DEFAULT_OUTPUT_FORMAT,
    ) -> Self:
        self = super().__new__(cls)
        self._output_format = output_format
        self._client = ElevenLabsClient(api_key=api_key, sdk=client)
        return self

    def _generate_sync(self, prompt: str, duration_ms: int, output_path: Path) -> Path:
        """Run the synchronous SDK call and file write.

        Extracted so ``generate_track`` can delegate to a thread via
        ``asyncio.to_thread``, keeping the event loop unblocked.
        """
        # The prompt is agent-authored content sent to ElevenLabs: never at INFO
        # (the durable file). INFO gets length + a short hash; full text at DEBUG.
        prompt_sha = hashlib.sha256(prompt.encode()).hexdigest()[:12]
        logger.info(
            "Generating music track: path=%s, duration_ms=%d, format=%s, "
            "prompt_len=%d, prompt_sha=%s",
            output_path,
            duration_ms,
            self._output_format,
            len(prompt),
            prompt_sha,
        )
        logger.debug("music prompt (sha %s): %r", prompt_sha, prompt)

        try:
            bytes_written = self._client.stream_to(
                output_path,
                lambda sdk: sdk.music.stream(
                    prompt=prompt,
                    music_length_ms=duration_ms,
                    force_instrumental=True,
                    output_format=self._output_format,
                ),
            )
        except ApiError:
            logger.exception("ElevenLabs music API call failed")
            raise

        logger.info("Wrote music track: %s (%d bytes)", output_path, bytes_written)
        return output_path

    async def generate_track(
        self, prompt: str, duration_ms: int, output_path: Path
    ) -> Path:
        """Generate a music track and write it to output_path.

        Delegates to a worker thread so the synchronous ElevenLabs SDK
        call and file I/O do not block the event loop.

        Args:
            prompt: Descriptive prompt for the track.
            duration_ms: Desired track length in milliseconds.
            output_path: Where to write the audio file.

        Returns:
            The path to the generated file.

        Raises:
            ProviderAuthError: When ElevenLabs rejects the API key (401).
            ApiError: On any other ElevenLabs API failure.
            CaBundleError: When the CA certificates in effect do not trust
                ElevenLabs, or ``SSL_CERT_FILE`` cannot be loaded.
            ProviderConfigError: When httpx refuses to send the request.
            ProviderTransportError: When the request fails in transport.
            RuntimeError: When the API returns no audio data.
        """
        return await asyncio.to_thread(
            self._generate_sync, prompt, duration_ms, output_path
        )
