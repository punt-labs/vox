"""Provider API key management for the vox daemon.

The daemon runs as a launchd/systemd service with a stripped environment —
no API keys.  This module manages ``~/.punt-labs/vox/keys.env``, a simple
KEY=VALUE file written at ``vox daemon install`` time from the caller's
environment and loaded at daemon startup before any provider is instantiated.
"""

from __future__ import annotations

import logging
from pathlib import Path

from punt_vox.logging_config import VOX_DATA_DIR

logger = logging.getLogger(__name__)

_KEYS_FILE = VOX_DATA_DIR / "keys.env"

PROVIDER_KEY_NAMES: frozenset[str] = frozenset(
    {
        "ELEVENLABS_API_KEY",
        "OPENAI_API_KEY",
        "AWS_PROFILE",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_DEFAULT_REGION",
        "TTS_PROVIDER",
        "TTS_MODEL",
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
