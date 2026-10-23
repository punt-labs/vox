"""Provider API key management for the vox daemon.

The daemon runs as a launchd/systemd service with a stripped environment --
no API keys. This module owns the format of ``~/.punt-labs/vox/keys.env``, a
simple ``KEY=VALUE`` file written at ``vox daemon install`` time from the
caller's environment and loaded at daemon startup before any provider is
instantiated. Both sides read it through :func:`parse_keys_env`.

The set of variable names ``vox daemon install`` snapshots into ``keys.env``
comes from :data:`punt_vox.providers.credentials.PROVIDER_KEY_NAMES`, which
in turn is derived from the same :class:`ProviderCredentials` dispatch that
answers the readiness gate. One source of truth, so the write side cannot
save a variable the gate does not read (or omit one it does).
"""

from __future__ import annotations

from punt_vox.providers.credentials import PROVIDER_KEY_NAMES, ProviderCredentials

__all__ = [
    "HEADER_CARRIED_KEY_NAMES",
    "PROVIDER_KEY_NAMES",
    "parse_keys_env",
]

# The ``keys.env`` names held to the HTTP-header character set on write.
HEADER_CARRIED_KEY_NAMES: frozenset[str] = (
    ProviderCredentials().header_carried_env_vars()
)


def parse_keys_env(text: str) -> dict[str, str]:
    """Return the ``KEY=VALUE`` pairs in *text*, each side stripped.

    Blank lines, ``#`` comments, lines with no ``=``, and lines with an empty
    key are skipped; a later line for the same key wins.
    """
    pairs = (line.strip().partition("=") for line in text.splitlines())
    return {
        key.strip(): value.strip()
        for key, sep, value in pairs
        if sep and key.strip() and not key.startswith("#")
    }
