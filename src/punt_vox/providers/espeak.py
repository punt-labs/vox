"""espeak-ng TTS provider for Linux."""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

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

__all__ = ["EspeakProvider"]

# Default speech rate for espeak-ng (words per minute).
# espeak-ng default is 175 WPM, same as macOS say.
_DEFAULT_WPM = 175

# Default voice per language (ISO 639-1 → espeak-ng voice name).
_DEFAULT_VOICES: dict[str, str] = {
    "de": "de",
    "en": "en",
    "es": "es",
    "fr": "fr",
    "it": "it",
    "ja": "ja",
    "ko": "ko",
    "nl": "nl",
    "pl": "pl",
    "pt": "pt",
    "ru": "ru",
    "sv": "sv",
    "tr": "tr",
    "zh": "zh",
}


@dataclass(frozen=True)
class EspeakVoiceConfig:
    """Maps an espeak-ng voice to its language."""

    name: str
    language: str


# Cache of discovered voices, keyed by lowercase name.
VOICES: dict[str, EspeakVoiceConfig] = {}

# Whether voices have been loaded from the system.
_voices_loaded: bool = False


def _find_espeak_binary() -> str | None:
    """Find the espeak-ng or espeak binary on PATH."""
    for name in ("espeak-ng", "espeak"):
        path = shutil.which(name)
        if path is not None:
            return path
    return None


def _load_voices_from_system() -> None:
    """Parse ``espeak-ng --voices`` output and populate the voice cache.

    Output format (fixed-width columns)::

        Pty  Language  Age/Gender  VoiceName   File   Other Languages
         5     en             M  english      default
         5     en-gb          M  english      other/en-gb
         5     de             M  german       other/de
    """
    global _voices_loaded
    if _voices_loaded:
        return

    binary = _find_espeak_binary()
    if binary is None:
        _voices_loaded = True
        return

    try:
        result = subprocess.run(
            [binary, "--voices"],
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

    lines = result.stdout.splitlines()
    if not lines:
        _voices_loaded = True
        return

    # Parse column positions from header to handle variable-width
    # Age/Gender field (may contain "M" or "40 M" with MBROLA voices).
    header = lines[0]
    lang_col = header.find("Language")
    voice_col = header.find("VoiceName")
    if lang_col < 0 or voice_col < 0:
        _voices_loaded = True
        return

    for line in lines[1:]:
        if len(line) <= voice_col:
            continue
        lang_part = line[lang_col:voice_col].split()
        voice_part = line[voice_col:].split()
        if not lang_part or not voice_part:
            continue
        lang = lang_part[0]
        voice_name = voice_part[0]
        key = voice_name.lower()
        # Extract ISO 639-1 from language code (e.g. "en-gb" -> "en")
        iso = lang.split("-")[0]
        if len(iso) == 2 and key not in VOICES:
            VOICES[key] = EspeakVoiceConfig(name=lang, language=iso)
        # Also register by language code for convenience
        lang_key = lang.lower()
        if lang_key not in VOICES:
            VOICES[lang_key] = EspeakVoiceConfig(name=lang, language=iso)

        # Register bare ISO 639-1 prefix for fallback (e.g. "en" from "en-us").
        # A truly bare entry (lang == iso, e.g. "en") always wins over a
        # qualified variant that was parsed first (e.g. "en-us").
        if len(iso) == 2 and (lang == iso or iso not in VOICES):
            VOICES[iso] = EspeakVoiceConfig(name=lang, language=iso)

    _voices_loaded = True
    logger.debug("Loaded %d voices from espeak-ng", len(VOICES))


def _rate_to_wpm(rate: int) -> int:
    """Convert percentage rate to words-per-minute for espeak-ng.

    rate=100 -> 175 WPM (normal), rate=90 -> 157 WPM.
    """
    return max(1, int(_DEFAULT_WPM * rate / 100))


class EspeakProvider:
    """espeak-ng TTS provider for Linux.

    Implements the TTSProvider protocol using the espeak-ng
    speech synthesizer. Zero configuration required. Produces
    intentionally robotic speech to nudge users toward configuring
    a real provider (ElevenLabs, OpenAI, Polly).

    Works on Linux (and other platforms where espeak-ng is installed).
    """

    def __init__(self) -> None:
        binary = _find_espeak_binary()
        if binary is None:
            msg = (
                "espeak-ng or espeak not found on PATH. "
                "Install with: sudo apt-get install espeak-ng"
            )
            raise ValueError(msg)
        self._binary: str = binary

    @property
    def name(self) -> str:
        return "espeak"

    @property
    def default_voice(self) -> str:
        """Discover the best available English voice from the system."""
        _load_voices_from_system()
        for candidate in ("en", "en-us", "en-gb"):
            if candidate in VOICES:
                return candidate
        # First en-* variant found
        for key in VOICES:
            if key.startswith("en-"):
                return key
        # Absolute fallback: first voice, or "en" if nothing is installed
        if VOICES:
            return next(iter(VOICES))
        return "en"

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
    ) -> tuple[EspeakVoiceConfig, int]:
        """Return the espeak voice config and resolved WPM for a request."""
        resolved_voice = request.voice or self.default_voice
        voice_cfg = self._resolve_voice_config(resolved_voice)
        rate = request.rate if request.rate is not None else 100
        return voice_cfg, _rate_to_wpm(rate)

    def play_directly(self, request: AudioRequest) -> int:
        """Play directly via espeak-ng's built-in audio output.

        Spawns ``espeak-ng -v <voice> -s <wpm> <text>`` without ``-w``,
        so espeak-ng writes to the default audio device instead of a
        WAV file. Bypasses the WAV -> ffmpeg -> MP3 -> ffplay pipeline
        entirely: same syscall and same audio session as a user shell.
        """
        voice_cfg, wpm = self._resolve_voice_and_rate(request)
        cmd = [
            self._binary,
            "-v",
            voice_cfg.name,
            "-s",
            str(wpm),
            request.text,
        ]
        logger.info(
            "espeak direct-play: voice=%s wpm=%d chars=%d",
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

    def synthesize(
        self, request: SynthesisRequest, output_path: Path
    ) -> SynthesisResult:
        """Synthesize text to an MP3 file using espeak-ng.

        Produces WAV via espeak-ng, then converts to MP3 via ffmpeg.
        """
        voice_cfg, wpm = self._resolve_voice_and_rate(request)

        output_path.parent.mkdir(parents=True, exist_ok=True)

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            wav_path = Path(tmp.name)

        try:
            subprocess.run(
                [
                    self._binary,
                    "-v",
                    voice_cfg.name,
                    "-s",
                    str(wpm),
                    "-w",
                    str(wav_path),
                    request.text,
                ],
                check=True,
                timeout=60,
            )
            logger.info(
                "espeak-ng: voice=%s, wpm=%d, chars=%d",
                voice_cfg.name,
                wpm,
                len(request.text),
            )

            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(wav_path),
                    "-codec:a",
                    "libmp3lame",
                    "-qscale:a",
                    "2",
                    str(output_path),
                ],
                check=True,
                capture_output=True,
                timeout=60,
            )
        finally:
            wav_path.unlink(missing_ok=True)

        logger.info("Wrote %s", output_path)

        language = request.language or voice_cfg.language
        return SynthesisResult(
            path=output_path,
            text=request.text,
            provider=AudioProviderId.espeak,
            voice=voice_cfg.name,
            language=language,
            metadata=request.metadata,
        )

    def resolve_voice(self, name: str, language: str | None = None) -> str:
        """Validate and resolve a voice name to its canonical form."""
        cfg = self._resolve_voice_config(name)
        if language is not None and cfg.language != language:
            msg = (
                f"Voice '{cfg.name}' does not support language "
                f"'{language}' (supports {cfg.language})"
            )
            raise ValueError(msg)
        return cfg.name

    def check_health(self) -> list[HealthCheck]:
        """Check espeak-ng availability."""
        checks: list[HealthCheck] = []

        binary = _find_espeak_binary()
        if binary:
            binary_name = Path(binary).name
            checks.append(HealthCheck(passed=True, message=f"{binary_name}: {binary}"))
            voice = self.default_voice
            try:
                cfg = self._resolve_voice_config(voice)
                checks.append(
                    HealthCheck(
                        passed=True,
                        message=f"default voice: {cfg.name} ({cfg.language})",
                    )
                )
            except VoiceNotFoundError:
                checks.append(
                    HealthCheck(
                        passed=False,
                        message=f"default voice not available: {voice}",
                    )
                )
        else:
            checks.append(
                HealthCheck(
                    passed=False,
                    message=(
                        "espeak-ng/espeak: not found on PATH "
                        "(install with: apt install espeak-ng)"
                    ),
                )
            )

        return checks

    def get_default_voice(self, language: str) -> str:
        """Get the default espeak-ng voice for a language."""
        key = language.lower()
        voice = _DEFAULT_VOICES.get(key)
        if voice is None:
            supported = ", ".join(sorted(_DEFAULT_VOICES))
            msg = f"No default voice for language '{language}'. Supported: {supported}"
            raise ValueError(msg)
        return voice

    def list_voices(self, language: str | None = None) -> list[str]:
        """List available espeak-ng voices."""
        _load_voices_from_system()
        if language is None:
            return sorted(VOICES)
        return sorted(name for name, cfg in VOICES.items() if cfg.language == language)

    def infer_language_from_voice(self, voice: str) -> str | None:
        """Infer ISO 639-1 language from an espeak-ng voice name."""
        cfg = self._resolve_voice_config(voice)
        return cfg.language

    def _resolve_voice_config(self, name: str) -> EspeakVoiceConfig:
        """Resolve a voice name to its EspeakVoiceConfig."""
        key = name.lower()
        if key in VOICES:
            return VOICES[key]

        _load_voices_from_system()

        if key in VOICES:
            return VOICES[key]

        raise VoiceNotFoundError(name, sorted(VOICES))
