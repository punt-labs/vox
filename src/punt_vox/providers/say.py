"""macOS say command TTS provider."""

from __future__ import annotations

import logging
import platform
import re
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from punt_vox.normalize import strip_vibe_tags
from punt_vox.output import resolve_output_path
from punt_vox.types import (
    AudioProviderId,
    AudioRequest,
    HealthCheck,
    SynthesisRequest,
    SynthesisResult,
    VoiceNotFoundError,
)

logger = logging.getLogger(__name__)

__all__ = ["SayProvider"]

# Default speech rate for macOS say (words per minute).
_DEFAULT_WPM = 175

# Regex to parse `say -v '?'` output lines.
# Format: "Fred                en_US    # Hello! My name is Fred."
_VOICE_LINE_RE = re.compile(r"^(.+?)\s{2,}(\w{2}_\w{2})\s+#")

# Default voice per language (ISO 639-1 → lowercase macOS voice name).
_DEFAULT_VOICES: dict[str, str] = {
    "de": "anna",
    "en": "samantha",
    "es": "monica",
    "fr": "amelie",
    "it": "alice",
    "ja": "kyoko",
    "ko": "yuna",
    "pt": "luciana",
    "ru": "milena",
    "zh": "tingting",
}


@dataclass(frozen=True)
class SayVoiceConfig:
    """Maps a macOS say voice to its locale."""

    name: str
    locale: str


def _locale_to_iso(locale: str) -> str:
    """Convert locale like 'en_US' to ISO 639-1 code 'en'."""
    return locale[:2].lower()


# Cache of discovered voices, keyed by lowercase name.
VOICES: dict[str, SayVoiceConfig] = {}

# Whether voices have been loaded from the system.
_voices_loaded: bool = False


def _load_voices_from_system() -> None:
    """Parse ``say -v '?'`` output and populate the voice cache."""
    global _voices_loaded
    if _voices_loaded:
        return

    try:
        result = subprocess.run(
            ["say", "-v", "?"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (
        subprocess.CalledProcessError,
        FileNotFoundError,
        subprocess.TimeoutExpired,
    ):
        _voices_loaded = True
        return

    for line in result.stdout.splitlines():
        match = _VOICE_LINE_RE.match(line)
        if match:
            name = match.group(1).strip()
            locale = match.group(2)
            key = name.lower()
            if key not in VOICES:
                VOICES[key] = SayVoiceConfig(name=name, locale=locale)

    _voices_loaded = True
    logger.debug("Loaded %d voices from macOS say", len(VOICES))


def _rate_to_wpm(rate: int) -> int:
    """Convert percentage rate to words-per-minute for say command.

    rate=100 -> 175 WPM (normal), rate=90 -> 157 WPM.
    """
    return max(1, int(_DEFAULT_WPM * rate / 100))


class SayProvider:
    """macOS say command TTS provider.

    Implements the TTSProvider protocol using the macOS built-in
    say command. Zero configuration required. Uses Fred as the
    default voice to clearly signal that a real provider should
    be configured.

    Only works on macOS (Darwin).
    """

    def __init__(self) -> None:
        if platform.system() != "Darwin":
            msg = (
                "SayProvider requires macOS. "
                "Use --provider espeak/elevenlabs/openai/polly on this platform."
            )
            raise ValueError(msg)
        if shutil.which("say") is None:
            msg = "say command not found on PATH"
            raise ValueError(msg)

    @property
    def name(self) -> str:
        return "say"

    @property
    def default_voice(self) -> str:
        """Discover the best available voice, preferring Samantha."""
        _load_voices_from_system()
        if "samantha" in VOICES:
            return "samantha"
        if "alex" in VOICES:
            return "alex"
        # First English voice found
        for key, cfg in VOICES.items():
            if _locale_to_iso(cfg.locale) == "en":
                return key
        # Absolute fallback: first voice, or "samantha" if nothing loaded
        if VOICES:
            return next(iter(VOICES))
        return "samantha"

    @property
    def supports_expressive_tags(self) -> bool:
        return False

    def generate_audio(self, request: SynthesisRequest) -> SynthesisResult:
        output_path = resolve_output_path(request)
        return self.synthesize(request, output_path)

    def generate_audios(
        self, requests: Sequence[SynthesisRequest]
    ) -> list[SynthesisResult]:
        return [self.generate_audio(request) for request in requests]

    def _resolve_voice_and_rate(
        self, request: AudioRequest
    ) -> tuple[SayVoiceConfig, int]:
        """Return the say voice config and resolved WPM for a request."""
        resolved_voice = request.voice or self.default_voice
        voice_cfg = self._resolve_voice_config(resolved_voice)
        rate = request.rate if request.rate is not None else 100
        return voice_cfg, _rate_to_wpm(rate)

    def play_directly(self, request: AudioRequest) -> int:
        """Play directly via the macOS ``say`` command.

        Spawns ``say -v <voice> -r <wpm> <text>`` without ``-o``, so
        say writes to the default audio device instead of an AIFF file.
        Bypasses the AIFF -> ffmpeg -> MP3 -> ffplay pipeline.
        """
        voice_cfg, wpm = self._resolve_voice_and_rate(request)
        cmd = [
            "say",
            "-v",
            voice_cfg.name,
            "-r",
            str(wpm),
            request.text,
        ]
        logger.info(
            "say direct-play: voice=%s wpm=%d chars=%d",
            voice_cfg.name,
            wpm,
            len(request.text),
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

    def synthesize(
        self, request: SynthesisRequest, output_path: Path
    ) -> SynthesisResult:
        """Synthesize text to an MP3 file using macOS say command.

        Produces AIFF via say, then converts to MP3 via ffmpeg.
        """
        text = strip_vibe_tags(request.text)

        voice_cfg, wpm = self._resolve_voice_and_rate(request)

        output_path.parent.mkdir(parents=True, exist_ok=True)

        with tempfile.NamedTemporaryFile(suffix=".aiff", delete=False) as tmp:
            aiff_path = Path(tmp.name)

        try:
            subprocess.run(
                [
                    "say",
                    "-v",
                    voice_cfg.name,
                    "-r",
                    str(wpm),
                    "-o",
                    str(aiff_path),
                    text,
                ],
                check=True,
                timeout=60,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
            logger.info(
                "say: voice=%s, wpm=%d, chars=%d",
                voice_cfg.name,
                wpm,
                len(text),
            )

            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(aiff_path),
                    "-codec:a",
                    "libmp3lame",
                    "-qscale:a",
                    "2",
                    str(output_path),
                ],
                check=True,
                capture_output=True,
                timeout=60,
                start_new_session=True,
            )
        finally:
            aiff_path.unlink(missing_ok=True)

        logger.info("Wrote %s", output_path)

        language = request.language or _locale_to_iso(voice_cfg.locale)
        return SynthesisResult(
            path=output_path,
            text=text,
            provider=AudioProviderId.say,
            voice=voice_cfg.name,
            language=language,
            metadata=request.metadata,
        )

    def resolve_voice(self, name: str, language: str | None = None) -> str:
        """Validate and resolve a voice name to its canonical form."""
        cfg = self._resolve_voice_config(name)
        if language is not None:
            voice_lang = _locale_to_iso(cfg.locale)
            if voice_lang != language:
                msg = (
                    f"Voice '{cfg.name}' does not support language '{language}' "
                    f"(supports {voice_lang})"
                )
                raise ValueError(msg)
        return cfg.name

    def check_health(self) -> list[HealthCheck]:
        """Check macOS say command availability."""
        checks: list[HealthCheck] = []

        if platform.system() != "Darwin":
            checks.append(
                HealthCheck(
                    passed=False,
                    message="macOS say: not available (requires macOS)",
                )
            )
            return checks

        say_path = shutil.which("say")
        if say_path:
            checks.append(HealthCheck(passed=True, message=f"macOS say: {say_path}"))
            voice = self.default_voice
            try:
                resolved = self.resolve_voice(voice)
                checks.append(
                    HealthCheck(
                        passed=True,
                        message=f"default voice: {resolved}",
                    )
                )
            except (ValueError, VoiceNotFoundError):
                checks.append(
                    HealthCheck(
                        passed=False,
                        message=f"default voice unavailable: {voice}",
                    )
                )
        else:
            checks.append(
                HealthCheck(
                    passed=False,
                    message="macOS say: not found on PATH",
                )
            )

        return checks

    def get_default_voice(self, language: str) -> str:
        """Get the default macOS voice for a language."""
        key = language.lower()
        voice = _DEFAULT_VOICES.get(key)
        if voice is None:
            supported = ", ".join(sorted(_DEFAULT_VOICES))
            msg = f"No default voice for language '{language}'. Supported: {supported}"
            raise ValueError(msg)
        return voice

    def list_voices(self, language: str | None = None) -> list[str]:
        """List available macOS say voices."""
        _load_voices_from_system()
        if language is None:
            return sorted(VOICES)
        return sorted(
            name
            for name, cfg in VOICES.items()
            if _locale_to_iso(cfg.locale) == language
        )

    def infer_language_from_voice(self, voice: str) -> str | None:
        """Infer ISO 639-1 language from a macOS voice name."""
        cfg = self._resolve_voice_config(voice)
        return _locale_to_iso(cfg.locale)

    def _resolve_voice_config(self, name: str) -> SayVoiceConfig:
        """Resolve a voice name to its SayVoiceConfig."""
        key = name.lower()
        if key in VOICES:
            return VOICES[key]

        _load_voices_from_system()

        if key in VOICES:
            return VOICES[key]

        raise VoiceNotFoundError(name, sorted(VOICES))
