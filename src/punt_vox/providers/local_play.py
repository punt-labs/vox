"""Direct-to-device playback for local TTS providers."""

from __future__ import annotations

import logging
import subprocess
from typing import TYPE_CHECKING, Self

from punt_vox.normalize import strip_vibe_tags
from punt_vox.providers.convert import rate_to_wpm
from punt_vox.types import AudioRequest, DirectPlayProvider

if TYPE_CHECKING:
    from punt_vox.providers.espeak import EspeakVoiceConfig
    from punt_vox.providers.say import SayVoiceConfig
    from punt_vox.providers.voice_resolver import VoiceResolver

__all__ = ["EspeakDirectPlayer", "SayDirectPlayer"]

logger = logging.getLogger(__name__)


class SayDirectPlayer(DirectPlayProvider):
    """Direct-to-device playback via macOS say command."""

    __slots__ = ("_voices",)

    _voices: VoiceResolver[SayVoiceConfig]

    def __new__(cls, *, voices: VoiceResolver[SayVoiceConfig]) -> Self:
        self = super().__new__(cls)
        self._voices = voices
        return self

    def play_directly(self, request: AudioRequest) -> int:
        """Play directly via the macOS ``say`` command.

        Spawns ``say -v <voice> -r <wpm> <text>`` without ``-o``, so
        say writes to the default audio device instead of an AIFF file.
        """
        resolved_voice = request.voice or self._voices.default_key
        voice_cfg = self._voices.resolve(resolved_voice)
        rate = request.rate if request.rate is not None else 100
        wpm = rate_to_wpm(rate)
        text = strip_vibe_tags(request.text)

        cmd = [
            "say",
            "-v",
            voice_cfg.name,
            "-r",
            str(wpm),
            text,
        ]
        logger.info(
            "say direct-play: voice=%s wpm=%d chars=%d",
            voice_cfg.name,
            wpm,
            len(text),
        )
        try:
            result = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                timeout=60,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            logger.error("say direct-play failed: %s", exc)
            return 1
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace").strip()
            logger.error(
                "say direct-play rc=%d stderr=%r",
                result.returncode,
                stderr,
            )
        return result.returncode


class EspeakDirectPlayer(DirectPlayProvider):
    """Direct-to-device playback via espeak-ng."""

    __slots__ = ("_binary", "_voices")

    _binary: str
    _voices: VoiceResolver[EspeakVoiceConfig]

    def __new__(cls, *, binary: str, voices: VoiceResolver[EspeakVoiceConfig]) -> Self:
        self = super().__new__(cls)
        self._binary = binary
        self._voices = voices
        return self

    def play_directly(self, request: AudioRequest) -> int:
        """Play directly via espeak-ng's built-in audio output.

        Spawns ``espeak-ng -v <voice> -s <wpm> <text>`` without ``-w``,
        so espeak-ng writes to the default audio device.
        """
        resolved_voice = request.voice or self._voices.default_key
        voice_cfg = self._voices.resolve(resolved_voice)
        rate = request.rate if request.rate is not None else 100
        wpm = rate_to_wpm(rate)
        text = strip_vibe_tags(request.text)

        cmd = [
            self._binary,
            "-v",
            voice_cfg.name,
            "-s",
            str(wpm),
            text,
        ]
        logger.info(
            "espeak direct-play: voice=%s wpm=%d chars=%d",
            voice_cfg.name,
            wpm,
            len(text),
        )
        try:
            result = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                timeout=60,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            logger.error("espeak direct-play failed: %s", exc)
            return 1
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace").strip()
            logger.error(
                "espeak direct-play rc=%d stderr=%r",
                result.returncode,
                stderr,
            )
        return result.returncode
