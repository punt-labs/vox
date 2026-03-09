"""TTS provider registry and auto-detection."""

from __future__ import annotations

import functools
import logging
import os
import platform
import shutil
import subprocess
import warnings
from collections.abc import Callable
from typing import TYPE_CHECKING

# elevenlabs SDK imports pydantic.v1 which warns on Python 3.14+.
# Their issue, not ours — suppress until they ship a fix.
warnings.filterwarnings(
    "ignore",
    message="Core Pydantic V1 functionality isn't compatible with Python 3.14",
    category=UserWarning,
    module=r"elevenlabs\.core\.pydantic_utilities",
)

if TYPE_CHECKING:
    from punt_vox.types import TTSProvider

__all__ = [
    "DEFAULT_VOICES",
    "auto_detect_provider",
    "format_voice_hint",
    "get_provider",
]

# Canonical default voice per provider, used in help text.
# Must stay in sync with each provider's default_voice property.
DEFAULT_VOICES: dict[str, str] = {
    "elevenlabs": "matilda",
    "polly": "joanna",
    "openai": "nova",
    "say": "samantha",
    "espeak": "en",
}


def format_voice_hint(names: list[str], limit: int = 10) -> str:
    """Format a truncated voice list for error messages."""
    sample = names[:limit]
    hint = ", ".join(sample)
    if len(names) > limit:
        hint += f" ... ({len(names)} total)"
    return hint


# Registry mapping provider name → factory callable.
# Factories are lazy (no imports at module level) to avoid loading
# boto3/openai/etc when the provider isn't used.
# Factories accept **kwargs to allow provider-specific options (e.g. model).
PROVIDER_REGISTRY: dict[str, Callable[..., TTSProvider]] = {}


def _register_polly(**kwargs: str | None) -> TTSProvider:
    from punt_vox.providers.polly import PollyProvider

    return PollyProvider()


def _register_openai(**kwargs: str | None) -> TTSProvider:
    from punt_vox.providers.openai import OpenAIProvider

    model = kwargs.get("model")
    return OpenAIProvider(model=model)


def _register_elevenlabs(**kwargs: str | None) -> TTSProvider:
    from punt_vox.providers.elevenlabs import ElevenLabsProvider

    model = kwargs.get("model")
    return ElevenLabsProvider(model=model)


def _register_say(**kwargs: str | None) -> TTSProvider:
    from punt_vox.providers.say import SayProvider

    return SayProvider()


def _register_espeak(**kwargs: str | None) -> TTSProvider:
    from punt_vox.providers.espeak import EspeakProvider

    return EspeakProvider()


PROVIDER_REGISTRY["polly"] = _register_polly
PROVIDER_REGISTRY["openai"] = _register_openai
PROVIDER_REGISTRY["elevenlabs"] = _register_elevenlabs
PROVIDER_REGISTRY["say"] = _register_say
PROVIDER_REGISTRY["espeak"] = _register_espeak


logger = logging.getLogger(__name__)


@functools.lru_cache(maxsize=1)
def _has_aws_credentials() -> bool:
    """Check whether AWS credentials are configured (cached)."""
    if not shutil.which("aws"):
        return False
    try:
        result = subprocess.run(
            ["aws", "sts", "get-caller-identity"],
            capture_output=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
    return result.returncode == 0


def auto_detect_provider() -> str:
    """Detect the provider from environment.

    Checks TTS_PROVIDER env var first, then probes for API keys:
    ElevenLabs > OpenAI > Polly (if AWS credentials valid) >
    system fallback (say on macOS, espeak on Linux).
    """
    env = os.environ.get("TTS_PROVIDER")
    if env:
        return env.lower()
    if os.environ.get("ELEVENLABS_API_KEY"):
        return "elevenlabs"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    if _has_aws_credentials():
        return "polly"
    if platform.system() == "Darwin" and shutil.which("say"):
        return "say"
    if platform.system() == "Linux" and (
        shutil.which("espeak-ng") or shutil.which("espeak")
    ):
        return "espeak"
    logger.warning("No TTS provider detected; falling back to polly")
    return "polly"


def get_provider(name: str | None = None, **kwargs: str | None) -> TTSProvider:
    """Look up a provider by name, or auto-detect.

    Resolution priority for provider name:
      1. Explicit ``name`` argument
      2. Session config (``.vox/config.md`` provider field)
      3. ``TTS_PROVIDER`` env var / API key auto-detection

    Resolution priority for model (passed via kwargs):
      1. Explicit ``model`` kwarg
      2. Session config (``.vox/config.md`` model field)
      3. ``TTS_MODEL`` env var / provider default

    Args:
        name: Provider name (e.g. 'polly', 'openai'). If None, checks
            session config then auto-detects.
        **kwargs: Provider-specific options (e.g. model='tts-1-hd').

    Returns:
        An initialized TTSProvider instance.

    Raises:
        ValueError: If the provider name is not registered.
    """
    # Read session config for fallback values.
    from punt_vox.config import read_config

    config = read_config()

    if name is not None:
        resolved = name.lower()
    elif config.provider:
        resolved = config.provider.lower()
    else:
        resolved = auto_detect_provider()

    # Fall back to config model only when the config provider matches the
    # resolved provider.  An ElevenLabs model name passed to OpenAI (or
    # vice-versa) would cause API errors.
    if kwargs.get("model") is None and config.model:
        config_provider = config.provider.lower() if config.provider else None
        if config_provider is None or config_provider == resolved:
            kwargs["model"] = config.model

    factory = PROVIDER_REGISTRY.get(resolved)
    if factory is None:
        available = ", ".join(sorted(PROVIDER_REGISTRY))
        msg = f"Unknown provider '{resolved}'. Available: {available}"
        raise ValueError(msg)
    return factory(**kwargs)
