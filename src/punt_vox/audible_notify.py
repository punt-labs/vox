"""Establish audible notifications when enabling a repo.

Enablement opens the hook gate, so absent or silent notify values become normal
notifications. Preserve an existing normal or continuous selection.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, final

from punt_vox.config import ConfigStore

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["AudibleNotify"]

# The notify values that already speak: normal ("y") and continuous ("c"). Only
# these are preserved on enable; an absent field or the silent "n" becomes "y".
_AUDIBLE_LEVELS: frozenset[str] = frozenset({"y", "c"})

# Enable defaults to task-completion and permission-prompt notifications.
_DEFAULT_LEVEL = "y"


@final
class AudibleNotify:
    """Ensure an enabled repo has an audible notify level, preserving any set one."""

    __slots__ = ("_store",)

    _store: ConfigStore

    def __new__(cls, config_dir: Path) -> Self:
        """Create an audible notify."""
        self = super().__new__(cls)
        self._store = ConfigStore(config_dir)
        return self

    def ensure_audible(self) -> None:
        """Set notify to the audible default unless an audible level is already set.

        Reads the stored ``notify`` through the config layer: an absent field or
        the silent ``"n"`` is raised to ``"y"``; an already-audible ``"y"`` or
        ``"c"`` is left untouched so a re-enable never downgrades a user's
        ``continuous`` choice.
        """
        if self._store.read_field("notify") not in _AUDIBLE_LEVELS:
            self._store.write_field("notify", _DEFAULT_LEVEL)
