"""TTS provider registry and auto-detection."""

from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
import warnings
from collections.abc import Callable
from typing import TYPE_CHECKING, Self

# elevenlabs SDK imports pydantic.v1 which warns on Python 3.14+.
# Their issue, not ours — suppress until they ship a fix.
warnings.filterwarnings(
    "ignore",
    message="Core Pydantic V1 functionality isn't compatible with Python 3.14",
    category=UserWarning,
    module=r"elevenlabs\.core\.pydantic_utilities",
)

if TYPE_CHECKING:
    from pathlib import Path

    from punt_vox.types import TTSProvider

__all__ = [
    "DEFAULT_VOICES",
    "ProviderRegistry",
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


logger = logging.getLogger(__name__)


class ProviderRegistry:
    """Provider registration, auto-detection, and resolution."""

    __slots__ = ("_factories", "_last_logged")

    _factories: dict[str, Callable[..., TTSProvider]]
    # The full outcome last logged -- (provider, reason, detected) -- so a real
    # state change with the SAME provider (the none->polly WARNING then a later
    # auto-detected-polly INFO) still emits, while a true repeat stays silent.
    _last_logged: tuple[str, str, bool] | None

    def __new__(cls) -> Self:
        self = super().__new__(cls)
        self._factories = {}
        self._last_logged = None
        return self

    def register(self, name: str, factory: Callable[..., TTSProvider]) -> None:
        """Register a provider factory by name."""
        self._factories[name] = factory

    def get(
        self,
        name: str | None = None,
        *,
        config_dir: Path | None = None,
        **kwargs: str | None,
    ) -> TTSProvider:
        """Resolve provider by name or auto-detect.

        Resolution priority for provider name:
          1. Explicit ``name`` argument
          2. Session config (``.punt-labs/vox/`` provider field)
          3. ``TTS_PROVIDER`` env var / API key auto-detection

        Resolution priority for model (passed via kwargs):
          1. Explicit ``model`` kwarg
          2. Session config (``.punt-labs/vox/`` model field)
          3. ``TTS_MODEL`` env var / provider default
        """
        from punt_vox.config import ConfigStore

        config = ConfigStore(config_dir).read()

        if name is not None:
            resolved = name.lower()
        elif config.provider:
            resolved = config.provider.lower()
        else:
            resolved = self.auto_detect()

        # Fall back to config model only when the config provider matches
        # the resolved provider. An ElevenLabs model name passed to OpenAI
        # (or vice-versa) would cause API errors.
        if kwargs.get("model") is None and config.model:
            config_provider = config.provider.lower() if config.provider else None
            if config_provider is None or config_provider == resolved:
                kwargs["model"] = config.model

        factory = self._factories.get(resolved)
        if factory is None:
            available = ", ".join(sorted(self._factories))
            msg = f"Unknown provider '{resolved}'. Available: {available}"
            raise ValueError(msg)
        return factory(**kwargs)

    def auto_detect(self) -> str:
        """Detect the provider from environment, logging the decision once.

        Checks TTS_PROVIDER env var first, then probes for API keys:
        ElevenLabs > OpenAI > Polly (if AWS credentials valid) >
        system fallback (say on macOS, espeak on Linux). The INFO decision line
        is deduplicated -- a stable choice logs once per process, not per call.
        """
        choice, reason, detected = self._resolve_choice()
        # Dedup on the FULL outcome (provider, reason, detected): emit only when it
        # changes, so a long-lived daemon logs ~1 line per distinct outcome, yet a
        # genuine transition with the same provider (none->polly WARNING, then a
        # later auto-detected-polly INFO) still surfaces the second line.
        outcome = (choice, reason, detected)
        if outcome != self._last_logged:
            if detected:
                logger.info("provider: auto-detected %s (%s)", choice, reason)
            else:
                logger.warning("provider: none detected, falling back to %s", choice)
            self._last_logged = outcome
        return choice

    def _resolve_choice(self) -> tuple[str, str, bool]:
        """Return the provider name, the reason chosen, and whether one was detected.

        The ``detected`` flag distinguishes a real detection (INFO) from the
        no-provider fallback (WARNING) without either branch logging directly --
        so the caller can deduplicate both the same way.
        """
        env = os.environ.get("TTS_PROVIDER")
        if env:
            return env.lower(), "TTS_PROVIDER env var", True
        if os.environ.get("ELEVENLABS_API_KEY"):
            return "elevenlabs", "ELEVENLABS_API_KEY set", True
        if os.environ.get("OPENAI_API_KEY"):
            return "openai", "OPENAI_API_KEY set", True
        if self._has_aws_credentials():
            return "polly", "AWS credentials valid", True
        if platform.system() == "Darwin" and shutil.which("say"):
            return "say", "system fallback (macOS)", True
        if platform.system() == "Linux" and (
            shutil.which("espeak-ng") or shutil.which("espeak")
        ):
            return "espeak", "system fallback (Linux)", True
        return "polly", "no provider detected", False

    @staticmethod
    def _has_aws_credentials() -> bool:
        """Check whether AWS credentials are configured; DEBUG why Polly is skipped."""
        if not shutil.which("aws"):
            logger.debug("provider: polly not chosen (aws cli absent)")
            return False
        try:
            result = subprocess.run(
                ["aws", "sts", "get-caller-identity"],
                capture_output=True,
                timeout=5,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            logger.debug("provider: polly not chosen (aws probe error: %s)", exc)
            return False
        if result.returncode != 0:
            logger.debug("provider: polly not chosen (aws probe: nonzero exit)")
        return result.returncode == 0


# -- Default registry with all 5 providers --------------------------------


def _register_polly(**_kwargs: str | None) -> TTSProvider:
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


def _register_say(**_kwargs: str | None) -> TTSProvider:
    from punt_vox.providers.say import SayProvider

    return SayProvider()


def _register_espeak(**_kwargs: str | None) -> TTSProvider:
    from punt_vox.providers.espeak import EspeakProvider

    return EspeakProvider()


_default_registry = ProviderRegistry()
_default_registry.register("polly", _register_polly)
_default_registry.register("openai", _register_openai)
_default_registry.register("elevenlabs", _register_elevenlabs)
_default_registry.register("say", _register_say)
_default_registry.register("espeak", _register_espeak)


def get_provider(
    name: str | None = None,
    config_dir: Path | None = None,
    **kwargs: str | None,
) -> TTSProvider:
    """Look up a provider by name, or auto-detect."""
    return _default_registry.get(name, config_dir=config_dir, **kwargs)


def auto_detect_provider() -> str:
    """Detect the best available provider from environment."""
    return _default_registry.auto_detect()
