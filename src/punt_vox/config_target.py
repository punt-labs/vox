"""Prepare a settings write in a Git checkout."""

from __future__ import annotations

import errno
from pathlib import Path
from typing import Self, final

from punt_vox.config_seed import InheritedConfig

__all__ = ["CONFIG_PATHS", "DEFAULT_CONFIG_DIR", "RepoConfigTarget"]

DEFAULT_CONFIG_DIR = Path(".punt-labs") / "vox"
CONFIG_PATHS = (DEFAULT_CONFIG_DIR / "vox.md", DEFAULT_CONFIG_DIR / "vox.local.md")
_LOCK_DIR = Path(".punt-labs/local/vox")


@final
class RepoConfigTarget:
    """A checkout's own settings directory, or the destination for a new one."""

    __slots__ = ("_root",)
    _root: Path

    def __new__(cls, root: Path) -> Self:
        """Bind a resolved Git root."""
        self = super().__new__(cls)
        self._root = root
        return self

    def prepare(self, found: Path | None) -> Path:
        """Reuse the config at *found* if local, otherwise prepare a new one.

        Raises:
            PermissionError: The destination resolves outside the checkout.
        """
        if found is not None and found.is_relative_to(self._root):
            if not (found / CONFIG_PATHS[1].name).exists() and found.resolve() == found:
                base = found.parent.parent
                self._check_contained(base / _LOCK_DIR)
                InheritedConfig(None).seed(base, found)
            return found
        paths = (DEFAULT_CONFIG_DIR, *CONFIG_PATHS, _LOCK_DIR)
        self._check_contained(*(self._root / path for path in paths))
        own = self._root / DEFAULT_CONFIG_DIR
        InheritedConfig(found).seed(self._root, own)
        return own

    def _check_contained(self, *paths: Path) -> None:
        """Reject paths that resolve outside the checkout, even if absent."""
        for path in paths:
            if not path.resolve().is_relative_to(self._root):
                msg = "Vox config destination is outside the Git checkout"
                raise PermissionError(errno.EPERM, msg, path)
