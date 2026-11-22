"""The common process environment, independent of harness credentials."""

from __future__ import annotations

from typing import final


@final
class ProcessEnv:
    """Admit only basic process, locale and XDG settings."""

    __slots__ = ()
    NAMES = frozenset(
        {"PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "TERM", "TMPDIR"}
    )
    PREFIXES = ("LC_", "XDG_")

    @classmethod
    def admits(cls, name: str) -> bool:
        """Return whether a name belongs to the harness-independent base."""
        return name in cls.NAMES or name.startswith(cls.PREFIXES)
