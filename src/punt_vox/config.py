"""Centralized read/write for .vox/config.md YAML frontmatter.

Python components that need config (e.g. server, CLI, watcher) import
from here.  Shell hooks (e.g. ``hooks/*.sh``) read the same file via
their own bash-based reader.  The canonical path is ``.vox/config.md``
in the current working directory.  All fields return safe defaults when
the file is missing.
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path(".vox/config.md")


def resolve_config_path() -> Path:
    """Resolve .vox/config.md at the main repo root (worktree-safe).

    Uses ``git rev-parse --git-common-dir`` to find the shared git
    directory, then resolves to its parent.  Falls back to cwd-relative
    ``.vox/config.md`` when git is unavailable or not in a repo.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        git_common = result.stdout.strip()
        if git_common:
            return Path(git_common).resolve().parent / ".vox" / "config.md"
    except (
        subprocess.CalledProcessError,
        FileNotFoundError,
        subprocess.TimeoutExpired,
    ):
        pass
    return DEFAULT_CONFIG_PATH


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
    text = path.read_text(encoding="utf-8")
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
        text = path.read_text(encoding="utf-8")
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


# ---------------------------------------------------------------------------
# Write helpers
# ---------------------------------------------------------------------------

ALLOWED_CONFIG_KEYS: frozenset[str] = frozenset(
    {
        "notify",
        "speak",
        "voice",
        "voice_enabled",
        "vibe",
        "vibe_tags",
        "vibe_mode",
        "vibe_signals",
    }
)

_CLOSING_FENCE_RE = re.compile(r"\n---\s*$", re.MULTILINE)


def write_field(key: str, value: str, config_path: Path | None = None) -> None:
    """Write a single YAML frontmatter field to .vox/config.md.

    Updates the field in-place if present, or inserts it before the
    closing ``---`` if absent. Creates the file with minimal frontmatter
    if it does not exist.
    """
    if key not in ALLOWED_CONFIG_KEYS:
        allowed = ", ".join(sorted(ALLOWED_CONFIG_KEYS))
        msg = f"Unknown config key '{key}'. Allowed: {allowed}"
        raise ValueError(msg)

    path = config_path or DEFAULT_CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    replacement = f'{key}: "{value}"'

    if not path.exists():
        path.write_text(f"---\n{replacement}\n---\n")
        return

    text = path.read_text()
    field_re = re.compile(rf"^{re.escape(key)}:\s*\"?[^\"\n]*\"?\s*$", re.MULTILINE)

    if field_re.search(text):
        text = field_re.sub(replacement, text)
    elif _CLOSING_FENCE_RE.search(text):
        text = _CLOSING_FENCE_RE.sub(f"\n{replacement}\n---", text, count=1)
    else:
        logger.warning("Malformed config (no closing ---): %s", path)
        text = f"---\n{replacement}\n---\n"

    path.write_text(text)
    logger.info("Config: set %s = %r in %s", key, value, path)


def write_fields(updates: dict[str, str], config_path: Path | None = None) -> None:
    """Write multiple YAML frontmatter fields in a single read-write cycle.

    Reads the file once, applies all regex substitutions, writes once.
    All keys are validated before any I/O so a single bad key aborts
    the entire batch.
    """
    for key in updates:
        if key not in ALLOWED_CONFIG_KEYS:
            allowed = ", ".join(sorted(ALLOWED_CONFIG_KEYS))
            msg = f"Unknown config key '{key}'. Allowed: {allowed}"
            raise ValueError(msg)

    path = config_path or DEFAULT_CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    if not path.exists():
        lines = [f'{k}: "{v}"' for k, v in updates.items()]
        path.write_text("---\n" + "\n".join(lines) + "\n---\n")
        return

    text = path.read_text()
    for key, value in updates.items():
        replacement = f'{key}: "{value}"'
        field_re = re.compile(rf"^{re.escape(key)}:\s*\"?[^\"\n]*\"?\s*$", re.MULTILINE)
        if field_re.search(text):
            text = field_re.sub(replacement, text)
        elif _CLOSING_FENCE_RE.search(text):
            text = _CLOSING_FENCE_RE.sub(f"\n{replacement}\n---", text, count=1)
        else:
            logger.warning("Malformed config (no closing ---): %s", path)
            lines = [f'{k}: "{v}"' for k, v in updates.items()]
            text = "---\n" + "\n".join(lines) + "\n---\n"
            break

    path.write_text(text)
    for key, value in updates.items():
        logger.info("Config: set %s = %r in %s", key, value, path)
