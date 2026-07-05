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
from typing import Self

from punt_vox.dirs import DEFAULT_CONFIG_DIR, find_config_dir

logger = logging.getLogger(__name__)

__all__ = [
    "ALLOWED_CONFIG_KEYS",
    "DEFAULT_CONFIG_DIR",
    "DURABLE_KEYS",
    "EPHEMERAL_KEYS",
    "ConfigStore",
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
        "voice",
    }
)

# The whole vibe cluster is session state, not a durable preference. Keeping
# vibe_mode in the tracked vox.md let any git checkout/stash resurrect a stale
# "manual" mode while the gitignored mood lingered (vox-73m5). Mode, mood, tags,
# and signals now live together in the ephemeral vox.local.md.
EPHEMERAL_KEYS: frozenset[str] = frozenset(
    {
        "vibe",
        "vibe_mode",
        "vibe_signals",
        "vibe_tags",
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
    repo_name: str | None = None


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


def _fields_to_config(
    fields: dict[str, str],
    *,
    repo_name: str | None = None,
) -> VoxConfig:
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
        repo_name=repo_name,
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
    _write_batch(path, {key: value})


def _write_batch(path: Path, updates: dict[str, str]) -> None:
    """Write multiple key-value pairs in a single read-write cycle."""
    path.parent.mkdir(parents=True, exist_ok=True)

    if not path.exists():
        lines = [f'{k}: "{v}"' for k, v in updates.items()]
        path.write_text("---\n" + "\n".join(lines) + "\n---\n", encoding="utf-8")
        return

    text = path.read_text(encoding="utf-8")
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


# ── Filenames ───────────────────────────────────────────────────────

DURABLE_FILENAME = "vox.md"
EPHEMERAL_FILENAME = "vox.local.md"


# ── ConfigStore ─────────────────────────────────────────────────────


class ConfigStore:
    """Owns a config directory and provides read/write access to vox config."""

    __slots__ = ("_dir", "_durable_path", "_ephemeral_path")

    _dir: Path
    _durable_path: Path
    _ephemeral_path: Path

    def __new__(cls, config_dir: Path | None = None) -> Self:
        self = super().__new__(cls)
        self._dir = config_dir or DEFAULT_CONFIG_DIR
        self._durable_path = self._dir / DURABLE_FILENAME
        self._ephemeral_path = self._dir / EPHEMERAL_FILENAME
        return self

    @property
    def dir(self) -> Path:
        """Return the config directory."""
        return self._dir

    def read(self) -> VoxConfig:
        """Read all config fields, merging durable and ephemeral files.

        Each file contributes only the keys it owns: durable prefs from
        ``vox.md``, session state from ``vox.local.md``.  Filtering both
        sides keeps the split drift-proof -- a stale ``vibe_mode`` left in a
        committed ``vox.md`` is ignored rather than resurrected (vox-73m5).
        """
        fields: dict[str, str] = {}

        durable = _parse_frontmatter(self._durable_path)
        fields.update({k: v for k, v in durable.items() if k in DURABLE_KEYS})

        local = _parse_frontmatter(self._ephemeral_path)
        fields.update({k: v for k, v in local.items() if k in EPHEMERAL_KEYS})

        return _fields_to_config(fields, repo_name=_derive_repo_name(self._dir))

    def read_field(self, field: str) -> str | None:
        """Read a single config field from the correct file."""
        if field in EPHEMERAL_KEYS:
            return _read_single_field(self._ephemeral_path, field)
        return _read_single_field(self._durable_path, field)

    def write_field(self, key: str, value: str) -> None:
        """Write a single config field to the correct file."""
        if key not in ALLOWED_CONFIG_KEYS:
            allowed = ", ".join(sorted(ALLOWED_CONFIG_KEYS))
            msg = f"Unknown config key '{key}'. Allowed: {allowed}"
            raise ValueError(msg)
        _validate_value(value)

        if key in EPHEMERAL_KEYS:
            _write_single(self._ephemeral_path, key, value)
        else:
            _write_single(self._durable_path, key, value)

    def write_fields(self, updates: dict[str, str]) -> None:
        """Write multiple config fields, routing each to the correct file."""
        for key, value in updates.items():
            if key not in ALLOWED_CONFIG_KEYS:
                allowed = ", ".join(sorted(ALLOWED_CONFIG_KEYS))
                msg = f"Unknown config key '{key}'. Allowed: {allowed}"
                raise ValueError(msg)
            _validate_value(value)

        durable_updates = {k: v for k, v in updates.items() if k in DURABLE_KEYS}
        ephemeral_updates = {k: v for k, v in updates.items() if k in EPHEMERAL_KEYS}
        if durable_updates:
            _write_batch(self._durable_path, durable_updates)
        if ephemeral_updates:
            _write_batch(self._ephemeral_path, ephemeral_updates)


# ── Module-level helpers ────────────────────────────────────────────


def _derive_repo_name(config_dir: Path) -> str | None:
    """Derive the repo name from config_dir.

    config_dir is ``<repo>/.punt-labs/vox/``, so the repo root is two
    parents up and its ``.name`` gives the repo directory name.  Returns
    None when the path is too shallow.
    """
    repo_root = config_dir.parent.parent
    # Guard against degenerate paths like "/" where .name is ""
    name = repo_root.name
    return name if name else None


def _validate_value(value: str) -> None:
    """Reject values that would corrupt YAML frontmatter."""
    if "\n" in value or "\r" in value:
        msg = f"Config values must not contain newlines, got: {value!r}"
        raise ValueError(msg)


# ── Backward-compatible module-level wrappers ───────────────────────


def read_field(field: str, config_dir: Path | None = None) -> str | None:
    """Read a single config field from the correct file."""
    return ConfigStore(config_dir).read_field(field)


def read_config(config_dir: Path | None = None) -> VoxConfig:
    """Read all config fields, merging vox.md and vox.local.md."""
    return ConfigStore(config_dir).read()


def write_field(key: str, value: str, config_dir: Path | None = None) -> None:
    """Write a single config field to the correct file."""
    ConfigStore(config_dir).write_field(key, value)


def write_fields(updates: dict[str, str], config_dir: Path | None = None) -> None:
    """Write multiple config fields, routing each to the correct file."""
    ConfigStore(config_dir).write_fields(updates)
