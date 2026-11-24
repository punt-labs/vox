"""Helpers for leading expressive tags and session vibe application."""

from __future__ import annotations

import re
from pathlib import Path

import punt_vox.config as _config

_LEADING_TAG_RE = re.compile(r"^\s*\[[^\]\n]+\]")
_LEADING_TAGS_RE = re.compile(r"^(\s*\[[^\]\n]+\]\s*)+")  # one-or-more tags


def split_leading_expressive_tags(text: str) -> tuple[str, str]:
    """Split leading bracket-style expressive tags off the front of *text*.

    Returns ``(tags, body)`` where ``tags`` is the leading bracket
    portion (e.g. ``"[serious] [calm]"``, with no trailing whitespace)
    and ``body`` is everything after. When ``text`` does not begin with
    a tag, returns ``("", text)``.

    Used by :func:`apply_vibe` to detect and preserve leading tags
    when composing vibe text. Normalization is handled
    inside ``voxd._apply_vibe_for_synthesis``, which uses
    ``VIBE_TAG_RE`` to split tags at any position — not just leading.
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


def apply_vibe(
    text: str,
    *,
    expressive_tags: bool,
    override_tags: str | None = None,
    config_dir: Path | None = None,
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
    tags = override_tags or _config.ConfigStore(
        config_dir or _config.DEFAULT_CONFIG_DIR
    ).read_field("vibe_tags")
    if tags and not _LEADING_TAG_RE.match(text):
        return f"{tags} {text}"
    return text
