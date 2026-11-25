"""Where the lock that serializes writes to one config file lives."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Self, final

from punt_vox.paths import run_dir
from punt_vox.sibling_lock import SiblingLock

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["ConfigLock"]


@final
class ConfigLock:
    """The lock file for a config path, shared across every spelling of it.

    A repo's ``.punt-labs/vox/`` may be read-only, so its lock lives in the
    pre-provisioned, writable local zone beside it; any other config path
    locks under the run directory, keyed by a digest of its resolved path so
    two custom configs never share a lock. The lock is world-writable: the
    daemon and a user's CLI must both be able to take it.
    """

    __slots__ = ("_target",)
    _target: Path

    def __new__(cls, config_path: Path) -> Self:
        """Resolve *config_path* once so every alias of it maps to one lock."""
        self = super().__new__(cls)
        self._target = config_path.resolve()
        return self

    @property
    def path(self) -> Path:
        """Return the lock file's path."""
        directory = self._target.parent
        if directory.name == "vox" and directory.parent.name == ".punt-labs":
            return directory.parent / "local" / "vox" / f"{self._target.name}.lock"
        digest = hashlib.sha256(str(self._target).encode()).hexdigest()
        return run_dir() / "config-locks" / digest

    def sibling(self) -> SiblingLock:
        """Return the exclusive lock to hold across a read-modify-write."""
        return SiblingLock(self._target, lock_path=self.path, mode=0o666)
