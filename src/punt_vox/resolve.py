"""Shared resolution helpers for CLI and MCP server.

Houses voice/language resolution, output path resolution, and vibe
application — logic that both surfaces need but that doesn't belong
in core.py (provider-agnostic orchestration) or config.py (file I/O).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import punt_vox.config as _config
from punt_vox.ephemeral import clean_ephemeral, ephemeral_output_dir
from punt_vox.output import default_output_dir
from punt_vox.types import TTSProvider, VoiceNotFoundError, validate_language

logger = logging.getLogger(__name__)

_LEADING_TAG_RE = re.compile(r"^\s*\[[^\]\n]+\]")


def resolve_voice_and_language(
    provider: TTSProvider,
    voice: str | None,
    language: str | None,
    *,
    config_path: Path | None = None,
) -> tuple[str, str | None]:
    """Resolve voice and language from user input.

    Priority: explicit voice > session voice > language default > provider default.

    If only language is provided, selects the provider's default voice for it.
    If only voice is provided, infers language from the voice (best-effort).
    If both, validates compatibility.

    When *config_path* is provided (or defaults to ``.vox/config.md``),
    reads the ``voice`` field as session default.
    """
    if language is not None:
        language = validate_language(language)

    voice_from_config = False
    if voice is None:
        voice = _config.read_field("voice", config_path or _config.DEFAULT_CONFIG_PATH)
        voice_from_config = voice is not None

    if voice is None and language is not None:
        voice = provider.get_default_voice(language)
    elif voice is None:
        voice = provider.default_voice

    try:
        if language is not None:
            provider.resolve_voice(voice, language)
        else:
            provider.resolve_voice(voice)
            language = provider.infer_language_from_voice(voice)
    except VoiceNotFoundError:
        if not voice_from_config:
            raise
        logger.info(
            "Session voice '%s' not available for %s; using default '%s'",
            voice,
            type(provider).__name__,
            provider.default_voice,
        )
        voice = provider.default_voice
        if language is not None:
            provider.resolve_voice(voice, language)
        else:
            provider.resolve_voice(voice)
            language = provider.infer_language_from_voice(voice)

    return voice, language


def resolve_output_dir(output_dir: str | None, *, ephemeral: bool = False) -> Path:
    """Resolve an output directory, using the default if not specified.

    When *ephemeral* is True, returns the ephemeral ``.vox/`` directory
    in the current working directory and ignores *output_dir*.
    """
    if ephemeral:
        clean_ephemeral()
        return ephemeral_output_dir()
    if output_dir:
        return Path(output_dir)
    return default_output_dir()


def apply_vibe(
    text: str,
    *,
    expressive_tags: bool,
    override_tags: str | None = None,
    config_path: Path | None = None,
) -> str:
    """Prepend vibe tags to text if the provider supports them.

    Only providers whose ``supports_expressive_tags`` is True interpret
    bracketed tags as performance cues. Other providers would speak
    them as literal words.

    Per-segment ``override_tags`` take priority over the session-level
    config tags.  Skips prepending when the text already starts with an
    expression tag (e.g. ``[calm]``) to avoid doubling.
    """
    if not expressive_tags:
        return text
    tags = override_tags or _config.read_field(
        "vibe_tags", config_path or _config.DEFAULT_CONFIG_PATH
    )
    if tags and not _LEADING_TAG_RE.match(text):
        return f"{tags} {text}"
    return text
