"""Centralized reader for .vox/config.md YAML frontmatter.

Every module that needs config (server, watcher, hooks) imports from
here.  The canonical path is ``.vox/config.md`` in the current working
directory.  All fields return safe defaults when the file is missing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CONFIG_PATH = Path(".vox/config.md")

_FIELD_RE = re.compile(r'^([a-z_]+):\s*"?([^"\n]*)"?\s*$', re.MULTILINE)


@dataclass(frozen=True)
class VoxConfig:
    """Snapshot of all config fields from .vox/config.md."""

    notify: str  # "y" | "c" | "n"
    speak: str  # "y" | "n"
    voice_enabled: str  # "true" | "false"
    vibe_mode: str  # "auto" | "manual" | "off"
    voice: str | None
    vibe: str | None
    vibe_tags: str | None
    vibe_signals: str | None


def read_field(field: str, config_path: Path | None = None) -> str | None:
    """Read a single YAML frontmatter field.  Returns None if absent."""
    path = config_path or DEFAULT_CONFIG_PATH
    if not path.exists():
        return None
    text = path.read_text()
    pattern = re.compile(rf"^{re.escape(field)}:\s*\"?([^\"\n]*)\"?\s*$", re.MULTILINE)
    match = pattern.search(text)
    if match and match.group(1).strip():
        return match.group(1).strip()
    return None


def read_config(config_path: Path | None = None) -> VoxConfig:
    """Read all config fields.  Returns defaults when file is missing."""
    path = config_path or DEFAULT_CONFIG_PATH
    fields: dict[str, str] = {}
    if path.exists():
        text = path.read_text()
        for match in _FIELD_RE.finditer(text):
            key = match.group(1)
            val = match.group(2).strip()
            if val:
                fields[key] = val

    notify = fields.get("notify", "n")
    if notify not in ("y", "c", "n"):
        notify = "n"

    speak = fields.get("speak", "y")
    if speak not in ("y", "n"):
        speak = "y"

    voice_enabled = fields.get("voice_enabled", "true")
    if voice_enabled not in ("true", "false"):
        voice_enabled = "true"

    vibe_mode = fields.get("vibe_mode", "auto")
    if vibe_mode not in ("auto", "manual", "off"):
        vibe_mode = "auto"

    return VoxConfig(
        notify=notify,
        speak=speak,
        voice_enabled=voice_enabled,
        vibe_mode=vibe_mode,
        voice=fields.get("voice"),
        vibe=fields.get("vibe"),
        vibe_tags=fields.get("vibe_tags"),
        vibe_signals=fields.get("vibe_signals"),
    )
