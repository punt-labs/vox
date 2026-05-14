"""macOS say command TTS provider."""

from __future__ import annotations

import logging
import platform
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from punt_vox.normalize import strip_vibe_tags
from punt_vox.output import OutputResolver
from punt_vox.providers.convert import ffmpeg_to_mp3, rate_to_wpm
from punt_vox.providers.voice_resolver import VoiceResolver
from punt_vox.types import (
    AudioProviderId,
    HealthCheck,
    SynthesisRequest,
    SynthesisResult,
    TTSProvider,
    VoiceNotFoundError,
)

logger = logging.getLogger(__name__)

__all__ = ["SayProvider"]

# Regex to parse `say -v '?'` output lines.
# Each line looks like: Fred                en_US    # Hello! My name is Fred.
_VOICE_LINE_RE = re.compile(r"^(.+?)\s{2,}(\w{2}_\w{2})\s+#")

# Default voice per language (ISO 639-1 -> lowercase macOS voice name).
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


class SayProvider(TTSProvider):
    """macOS say command TTS provider.

    Implements the TTSProvider protocol using the macOS built-in
    say command. Zero configuration required. Uses Samantha as the
    default voice.

    Only works on macOS (Darwin).
    """

    _voices: VoiceResolver[SayVoiceConfig]

    def __new__(cls) -> Self:
        if platform.system() != "Darwin":
            msg = (
                "SayProvider requires macOS. "
                "Use --provider espeak/elevenlabs/openai/polly on this platform."
            )
            raise ValueError(msg)
        if shutil.which("say") is None:
            msg = "say command not found on PATH"
            raise ValueError(msg)
        self = super().__new__(cls)

        self._voices = VoiceResolver(
            self._fetch_voices,
            default_key="samantha",
            ttl_seconds=0,
            cooldown_seconds=60,
        )
        return self

    @property
    def name(self) -> str:
        return "say"

    @property
    def default_voice(self) -> str:
        """Discover the best available voice, preferring Samantha."""
        cache = self._voices._cache  # pyright: ignore[reportPrivateUsage]
        if not cache:
            # Trigger a load so the cache is populated.
            self._voices._ensure_loaded()  # pyright: ignore[reportPrivateUsage]
            cache = self._voices._cache  # pyright: ignore[reportPrivateUsage]
        if "samantha" in cache:
            return "samantha"
        if "alex" in cache:
            return "alex"
        # First English voice found
        for key, cfg in cache.items():
            if _locale_to_iso(cfg.locale) == "en":
                return key
        # Absolute fallback: first voice, or "samantha" if nothing loaded
        if cache:
            return next(iter(cache))
        return "samantha"

    @property
    def supports_expressive_tags(self) -> bool:
        return False

    def generate_audio(self, request: SynthesisRequest) -> SynthesisResult:
        output_path = OutputResolver.resolve(request)
        return self.synthesize(request, output_path)

    def synthesize(
        self, request: SynthesisRequest, output_path: Path
    ) -> SynthesisResult:
        """Synthesize text to an MP3 file using macOS say command.

        Produces AIFF via say, then converts to MP3 via ffmpeg.
        """
        text = strip_vibe_tags(request.text)

        resolved_voice = request.voice or self.default_voice
        voice_cfg = self._voices.resolve(resolved_voice)
        rate = request.rate if request.rate is not None else 100
        wpm = rate_to_wpm(rate)

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

            ffmpeg_to_mp3(aiff_path, output_path)
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
        cfg = self._voices.resolve(name)
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
        all_names = self._voices.list_all()
        if language is None:
            return all_names
        return sorted(
            name
            for name in all_names
            if _locale_to_iso(self._voices.resolve(name).locale) == language
        )

    def infer_language_from_voice(self, voice: str) -> str | None:
        """Infer ISO 639-1 language from a macOS voice name."""
        cfg = self._voices.resolve(voice)
        return _locale_to_iso(cfg.locale)

    def _fetch_voices(self) -> dict[str, SayVoiceConfig]:
        """Parse ``say -v '?'`` output and return the voice dict."""
        fresh: dict[str, SayVoiceConfig] = {}

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
            return fresh

        for line in result.stdout.splitlines():
            match = _VOICE_LINE_RE.match(line)
            if match:
                vname = match.group(1).strip()
                locale = match.group(2)
                key = vname.lower()
                if key not in fresh:
                    fresh[key] = SayVoiceConfig(
                        name=vname,
                        locale=locale,
                    )

        logger.debug("Fetched %d voices from macOS say", len(fresh))
        return fresh
