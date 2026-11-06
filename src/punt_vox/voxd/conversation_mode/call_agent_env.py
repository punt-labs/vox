"""The call agent's environment: an allowlist, never the parent's minus a deny-list.

A developer shell carries GitHub tokens, database passwords, and API keys for
a dozen services; a deny-list misses whichever one nobody thought of, and the
call agent's output is spoken aloud. The child gets only what a process needs
to run -- ``PATH``, ``HOME``, the locale, the terminal, temp and XDG
directories -- plus the handful of pi's own settings a headless agent uses,
named one by one. Never a ``PI_`` prefix: that would pass any ``PI_API_KEY``
or ``PI_*_TOKEN`` a shell happens to hold, and pi reads no such variable.
pi variables it does read but a call must not inherit -- the session to
resume, a gateway, the TUI's display settings -- stay out too.

No credential is passed at all. pi authenticates from its own configuration
under ``~/.pi/agent`` (``models.json``, ``auth.json``), reached through
``HOME``; verified live with nothing but this allowlist. A pi set up to read a
provider key from the environment instead fails to start, cleanly, before the
call opens -- ``pi auth`` moves the key into pi's own store.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, final

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["CallAgentEnv"]


@final
class CallAgentEnv:
    """The allowlist a call agent's environment is built from."""

    __slots__ = ()

    NAMES = frozenset(
        {
            "PATH",
            "HOME",
            "USER",
            "LOGNAME",
            "SHELL",
            "LANG",
            "TERM",
            "TMPDIR",
            # pi's documented settings (docs/environment-variables.md) that
            # shape a headless RPC run: where its config and credentials
            # live, which model, and its offline/telemetry switches.
            "PI_CODING_AGENT_DIR",
            "PI_PACKAGE_DIR",
            "PI_PROVIDER",
            "PI_MODEL",
            "PI_REASONING_LEVEL",
            "PI_CACHE_RETENTION",
            "PI_OFFLINE",
            "PI_SKIP_VERSION_CHECK",
            "PI_TELEMETRY",
        }
    )
    PREFIXES = ("LC_", "XDG_")

    @classmethod
    def allowlisted(cls, parent: Mapping[str, str]) -> dict[str, str]:
        """Return the variables of *parent* the allowlist admits."""
        return {name: value for name, value in parent.items() if cls.admits(name)}

    @classmethod
    def admits(cls, name: str) -> bool:
        """Return whether *name* is allowlisted, by exact name or by prefix."""
        return name in cls.NAMES or name.startswith(cls.PREFIXES)
