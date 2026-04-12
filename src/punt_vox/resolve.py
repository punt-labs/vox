"""Shared resolution helpers for CLI and MCP server.

Houses voice/language resolution and vibe application -- logic that
both surfaces need but that doesn't belong in core.py (provider-agnostic
orchestration) or config.py (file I/O).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import punt_vox.config as _config
from punt_vox.types import TTSProvider, VoiceNotFoundError, validate_language

logger = logging.getLogger(__name__)

_LEADING_TAG_RE = re.compile(r"^\s*\[[^\]\n]+\]")
_LEADING_TAGS_RE = re.compile(r"^(\s*\[[^\]\n]+\]\s*)+")  # one-or-more tags


def split_leading_expressive_tags(text: str) -> tuple[str, str]:
    """Split leading bracket-style expressive tags off the front of *text*.

    Returns ``(tags, body)`` where ``tags`` is the leading bracket
    portion (e.g. ``"[serious] [calm]"``, with no trailing whitespace)
    and ``body`` is everything after. When ``text`` does not begin with
    a tag, returns ``("", text)``.

    This split exists so callers can pull the tags off BEFORE running
    the body through :func:`punt_vox.normalize.normalize_for_speech`,
    which discards brackets as non-prosody punctuation. Without the
    early split, ``[serious] hello`` becomes ``serious hello`` after
    normalization and the brackets cannot be stripped — the literal
    word ``serious`` survives into the final TTS input.
    """
    match = _LEADING_TAGS_RE.match(text)
    if not match:
        return "", text
    return match.group(0).strip(), text[match.end() :]


def strip_expressive_tags(text: str) -> str:
    """Remove leading bracket-style expressive tags from *text*.

    For use when the active provider+model does not interpret bracket
    tags as performance cues. Without stripping, a model like
    ``eleven_flash_v2_5`` (or any non-ElevenLabs provider) would speak
    ``[serious] Hello world`` as the literal phrase
    ``serious Hello world``.

    Returns the original text if stripping would leave the result
    empty (degenerate case where the text was nothing but tags).
    Stand-alone helper that ``apply_vibe`` and external callers
    (e.g. ``voxd``) both use, with no config-file or session-state
    coupling.
    """
    _tags, body = split_leading_expressive_tags(text)
    return body if body.strip() else text


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

    When *config_path* is provided (or defaults to
    ``.punt-labs/vox/config.md``), reads the ``voice`` field as session
    default.
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


def apply_vibe(
    text: str,
    *,
    expressive_tags: bool,
    override_tags: str | None = None,
    config_path: Path | None = None,
) -> str:
    """Prepend vibe tags to text if the provider supports them.

    Only providers whose ``supports_expressive_tags`` is True interpret
    bracketed tags as performance cues.  When ``expressive_tags`` is
    False, any leading bracket tags are stripped so non-supporting
    providers don't speak them as literal words.  If stripping would
    leave the text empty, the original text is returned unchanged.

    Per-segment ``override_tags`` take priority over the session-level
    config tags.  Skips prepending when the text already starts with an
    expression tag (e.g. ``[calm]``) to avoid doubling.
    """
    if not expressive_tags:
        return strip_expressive_tags(text)
    tags = override_tags or _config.read_field(
        "vibe_tags", config_path or _config.DEFAULT_CONFIG_PATH
    )
    if tags and not _LEADING_TAG_RE.match(text):
        return f"{tags} {text}"
    return text
