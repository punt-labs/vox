"""Read/write for split vox config: ``vox.md`` (durable) + ``vox.local.md`` (ephemeral).

Python components that need config import from here.  Shell hooks read
the same files via their own bash-based reader.  The canonical directory
is ``.punt-labs/vox/`` in the repo root; ``find_config_dir()`` walks up
from cwd to locate it.  All fields return safe defaults when files are
missing.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from punt_vox.dirs import DEFAULT_CONFIG_DIR, find_config_dir

logger = logging.getLogger(__name__)

__all__ = [
    "ALLOWED_CONFIG_KEYS",
    "DEFAULT_CONFIG_DIR",
    "DURABLE_KEYS",
    "EPHEMERAL_KEYS",
    "VoxConfig",
    "find_config_dir",
    "read_config",
    "read_field",
    "write_field",
    "write_fields",
]


_FIELD_RE = re.compile(r'^([a-z_]+):\s*"?([^"\n]*)"?\s*$', re.MULTILINE)
_CLOSING_FENCE_RE = re.compile(r"\n---\s*$", re.MULTILINE)

# ── Field-to-file routing ────────────────────────────────────────────

DURABLE_KEYS: frozenset[str] = frozenset(
    {
        "model",
        "notify",
        "provider",
        "speak",
        "vibe_mode",
        "voice",
    }
)

EPHEMERAL_KEYS: frozenset[str] = frozenset(
    {
        "vibe",
        "vibe_tags",
        "vibe_signals",
    }
)

ALLOWED_CONFIG_KEYS: frozenset[str] = DURABLE_KEYS | EPHEMERAL_KEYS


@dataclass(frozen=True)
class VoxConfig:
    """Snapshot of all config fields from vox.md + vox.local.md."""

    notify: str  # "y" | "c" | "n"
    speak: str  # "y" | "n"
    vibe_mode: str  # "auto" | "manual" | "off"
    voice: str | None
    provider: str | None
    model: str | None
    vibe: str | None
    vibe_tags: str | None
    vibe_signals: str | None


# ── Internal helpers ─────────────────────────────────────────────────


def _parse_frontmatter(path: Path) -> dict[str, str]:
    """Parse YAML frontmatter fields from *path*.  Returns empty dict if missing."""
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    fields: dict[str, str] = {}
    for match in _FIELD_RE.finditer(text):
        val = match.group(2).strip()
        if val:
            fields[match.group(1)] = val
    return fields


def _fields_to_config(fields: dict[str, str]) -> VoxConfig:
    """Build a VoxConfig from raw field dict, applying validation and defaults."""
    notify = fields.get("notify", "n")
    if notify not in ("y", "c", "n"):
        notify = "n"

    speak = fields.get("speak", "y")
    if speak not in ("y", "n"):
        speak = "y"

    vibe_mode = fields.get("vibe_mode", "auto")
    if vibe_mode not in ("auto", "manual", "off"):
        vibe_mode = "auto"

    return VoxConfig(
        notify=notify,
        speak=speak,
        vibe_mode=vibe_mode,
        voice=fields.get("voice"),
        provider=fields.get("provider"),
        model=fields.get("model"),
        vibe=fields.get("vibe"),
        vibe_tags=fields.get("vibe_tags"),
        vibe_signals=fields.get("vibe_signals"),
    )


def _read_single_field(path: Path, field: str) -> str | None:
    """Read a single YAML frontmatter field from *path*.  Returns None if absent."""
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(rf"^{re.escape(field)}:\s*\"?([^\"\n]*)\"?\s*$", re.MULTILINE)
    match = pattern.search(text)
    if match and match.group(1).strip():
        return match.group(1).strip()
    return None


def _write_single(path: Path, key: str, value: str) -> None:
    """Write a single key-value pair to the YAML frontmatter in *path*."""
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


def _write_batch(path: Path, updates: dict[str, str]) -> None:
    """Write multiple key-value pairs in a single read-write cycle."""
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


# ── Public API ───────────────────────────────────────────────────────


def read_field(field: str, config_dir: Path | None = None) -> str | None:
    """Read a single config field from the correct file."""
    d = config_dir or DEFAULT_CONFIG_DIR
    if field in EPHEMERAL_KEYS:
        return _read_single_field(d / "vox.local.md", field)
    return _read_single_field(d / "vox.md", field)


def read_config(config_dir: Path | None = None) -> VoxConfig:
    """Read all config fields, merging vox.md and vox.local.md."""
    d = config_dir or DEFAULT_CONFIG_DIR
    fields: dict[str, str] = {}

    # Base layer: durable prefs
    fields.update(_parse_frontmatter(d / "vox.md"))

    # Overlay: ephemeral session state (wins on conflict)
    fields.update(_parse_frontmatter(d / "vox.local.md"))

    return _fields_to_config(fields)


def write_field(key: str, value: str, config_dir: Path | None = None) -> None:
    """Write a single config field to the correct file."""
    if key not in ALLOWED_CONFIG_KEYS:
        allowed = ", ".join(sorted(ALLOWED_CONFIG_KEYS))
        msg = f"Unknown config key '{key}'. Allowed: {allowed}"
        raise ValueError(msg)

    d = config_dir or DEFAULT_CONFIG_DIR
    if key in EPHEMERAL_KEYS:
        _write_single(d / "vox.local.md", key, value)
    else:
        _write_single(d / "vox.md", key, value)


def write_fields(updates: dict[str, str], config_dir: Path | None = None) -> None:
    """Write multiple config fields, routing each to the correct file."""
    for key in updates:
        if key not in ALLOWED_CONFIG_KEYS:
            allowed = ", ".join(sorted(ALLOWED_CONFIG_KEYS))
            msg = f"Unknown config key '{key}'. Allowed: {allowed}"
            raise ValueError(msg)

    d = config_dir or DEFAULT_CONFIG_DIR
    durable_updates = {k: v for k, v in updates.items() if k in DURABLE_KEYS}
    ephemeral_updates = {k: v for k, v in updates.items() if k in EPHEMERAL_KEYS}
    if durable_updates:
        _write_batch(d / "vox.md", durable_updates)
    if ephemeral_updates:
        _write_batch(d / "vox.local.md", ephemeral_updates)
