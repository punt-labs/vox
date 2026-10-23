"""ElevenLabs Music generation provider."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Self

from elevenlabs.core import ApiError  # pyright: ignore[reportMissingTypeStubs]

from punt_vox.providers.elevenlabs_client import ElevenLabsClient
from punt_vox.types import MusicProvider

if TYPE_CHECKING:
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
        # The test seam: absent, the real SDK client is built on first use.
        client: ElevenLabs | None = None,
        output_format: str = _DEFAULT_OUTPUT_FORMAT,
    ) -> Self:
        self = super().__new__(cls)
        self._output_format = output_format
        self._client = ElevenLabsClient(sdk=client)
        return self

    def _generate_sync(self, prompt: str, duration_ms: int, output_path: Path) -> Path:
        """Run the synchronous SDK call and file write.

        Extracted so ``generate_track`` can delegate to a thread via
        ``asyncio.to_thread``, keeping the event loop unblocked.
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)

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
            response = self._client.sdk().music.stream(
                prompt=prompt,
                music_length_ms=duration_ms,
                force_instrumental=True,
                output_format=self._output_format,
            )
        except ApiError:
            logger.exception("ElevenLabs music API call failed")
            raise

        # Stream to a temp file alongside the target, rename on success.
        fd, tmp_name = tempfile.mkstemp(dir=output_path.parent, suffix=".tmp")
        tmp_path = Path(tmp_name)
        bytes_written = 0

        try:
            os.close(fd)  # close mkstemp fd; reopen via pathlib below
            with tmp_path.open("wb") as f:
                for chunk in response:
                    f.write(chunk)
                    bytes_written += len(chunk)

            if bytes_written == 0:
                msg = "ElevenLabs music API returned no audio data"
                raise RuntimeError(msg)

            tmp_path.rename(output_path)
        except BaseException:
            # Clean up the temp file on any failure (including KeyboardInterrupt).
            tmp_path.unlink(missing_ok=True)
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
            ApiError: On ElevenLabs API failure.
            CaBundleError: When ``SSL_CERT_FILE`` names an unloadable CA bundle.
            RuntimeError: When the API returns no audio data.
        """
        return await asyncio.to_thread(
            self._generate_sync, prompt, duration_ms, output_path
        )
