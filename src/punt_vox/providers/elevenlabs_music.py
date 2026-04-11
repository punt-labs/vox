"""ElevenLabs Music generation provider."""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from elevenlabs.core import ApiError  # pyright: ignore[reportMissingTypeStubs]

logger = logging.getLogger(__name__)

__all__ = ["ElevenLabsMusicProvider"]

_DEFAULT_OUTPUT_FORMAT = "mp3_44100_128"


class ElevenLabsMusicProvider:
    """ElevenLabs Music provider.

    Implements the MusicProvider protocol using the ElevenLabs SDK's
    music.stream endpoint. Streams bytes to a temp file in the same
    directory as the target, then renames atomically on completion.

    Always passes force_instrumental=True — background music should
    never have vocals.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        client: Any | None = None,  # pyright: ignore[reportExplicitAny]
        output_format: str = _DEFAULT_OUTPUT_FORMAT,
    ) -> None:
        self._output_format = output_format
        if client is not None:
            self._client: Any = client  # pyright: ignore[reportExplicitAny]
        else:
            from elevenlabs import ElevenLabs  # pyright: ignore[reportMissingTypeStubs]

            key = api_key or os.environ.get("ELEVENLABS_API_KEY")
            self._client = ElevenLabs(api_key=key)  # pyright: ignore[reportUnknownMemberType]

    def _generate_sync(self, prompt: str, duration_ms: int, output_path: Path) -> Path:
        """Run the synchronous SDK call and file write.

        Extracted so ``generate_track`` can delegate to a thread via
        ``asyncio.to_thread``, keeping the event loop unblocked.
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)

        logger.info(
            "Generating music track: duration_ms=%d, format=%s",
            duration_ms,
            self._output_format,
        )

        try:
            response: Any = self._client.music.stream(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
                prompt=prompt,
                music_length_ms=duration_ms,
                force_instrumental=True,
                output_format=self._output_format,
            )
        except ApiError:
            logger.exception("ElevenLabs music API call failed")
            raise

        # Stream to a temp file alongside the target, rename on success.
        fd, tmp_name = tempfile.mkstemp(
            dir=output_path.parent,
            suffix=".tmp",
        )
        tmp_path = Path(tmp_name)
        bytes_written = 0

        try:
            with open(fd, "wb") as f:
                for chunk in response:  # pyright: ignore[reportUnknownVariableType]
                    f.write(chunk)  # pyright: ignore[reportUnknownArgumentType]
                    bytes_written += len(chunk)  # pyright: ignore[reportUnknownArgumentType]

            if bytes_written == 0:
                msg = "ElevenLabs music API returned no audio data"
                raise RuntimeError(msg)

            tmp_path.rename(output_path)
        except BaseException:
            # Clean up the temp file on any failure (including KeyboardInterrupt).
            tmp_path.unlink(missing_ok=True)
            raise

        logger.info(
            "Wrote music track: %s (%d bytes)",
            output_path,
            bytes_written,
        )
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
            RuntimeError: When the API returns no audio data.
        """
        return await asyncio.to_thread(
            self._generate_sync, prompt, duration_ms, output_path
        )
