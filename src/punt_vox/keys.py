"""Provider API key management for the vox daemon.

The daemon runs as a launchd/systemd service with a stripped environment —
no API keys.  This module manages ``~/.punt-vox/keys.env``, a simple
KEY=VALUE file written at ``vox daemon install`` time from the caller's
environment and loaded at daemon startup before any provider is instantiated.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from pathlib import Path

logger = logging.getLogger(__name__)

_KEYS_FILE = Path.home() / ".punt-vox" / "keys.env"

_PROVIDER_KEY_NAMES: frozenset[str] = frozenset(
    {
        "ELEVENLABS_API_KEY",
        "OPENAI_API_KEY",
        "AWS_PROFILE",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_DEFAULT_REGION",
        "TTS_PROVIDER",
    }
)


def keys_file_path() -> Path:
    """Return the path to the keys.env file."""
    return _KEYS_FILE


def parse_keys_env(text: str) -> dict[str, str]:
    """Parse KEY=VALUE lines.  Skip comments and blank lines."""
    result: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip()
        if key:
            result[key] = value
    return result


def format_keys_env(keys: dict[str, str]) -> str:
    """Format as KEY=VALUE lines, sorted, with header comment."""
    header = (
        "# vox provider keys — loaded by daemon at startup\n"
        "# Written by: vox daemon install\n\n"
    )
    lines = [f"{k}={v}" for k, v in sorted(keys.items()) if v]
    return header + "\n".join(lines) + "\n"


def write_keys_env(env: Mapping[str, str]) -> Path:
    """Write keys.env from *env*, merging with existing file.  chmod 0600."""
    path = keys_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    existing: dict[str, str] = {}
    if path.exists():
        existing = parse_keys_env(path.read_text())

    merged = dict(existing)
    for k in _PROVIDER_KEY_NAMES:
        if k in env:
            if env[k]:  # non-empty: set it
                merged[k] = env[k]
            else:  # empty string: remove it
                merged.pop(k, None)
        # k not in env: preserve existing value

    path.write_text(format_keys_env(merged))
    path.chmod(0o600)
    return path


def load_keys_env() -> frozenset[str]:
    """Load keys.env into ``os.environ`` for keys not already set.

    Returns the names of variables that were loaded.
    """
    path = keys_file_path()
    if not path.exists():
        return frozenset()
    parsed = parse_keys_env(path.read_text())
    loaded: set[str] = set()
    for k, v in parsed.items():
        if v and k not in os.environ:
            os.environ[k] = v
            loaded.add(k)
    return frozenset(loaded)
