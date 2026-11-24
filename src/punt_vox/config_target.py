"""Prepare the destination for a first settings write at a Git root."""

from __future__ import annotations

import errno
from pathlib import Path
from typing import Self, final

from punt_vox.config_seed import InheritedConfig

__all__ = ["CONFIG_PATHS", "DEFAULT_CONFIG_DIR", "RepoConfigTarget"]

DEFAULT_CONFIG_DIR = Path(".punt-labs") / "vox"
CONFIG_PATHS = (DEFAULT_CONFIG_DIR / "vox.md", DEFAULT_CONFIG_DIR / "vox.local.md")


@final
class RepoConfigTarget:
    """The root config destination for a checkout without its own settings."""

    __slots__ = ("_root",)
    _root: Path

    def __new__(cls, root: Path) -> Self:
        """Bind a resolved Git root."""
        self = super().__new__(cls)
        self._root = root
        return self

    def prepare(self, inherited: Path | None) -> Path:
        """Seed inherited settings or verify a fresh destination stays in Git.

        Raises:
            PermissionError: A fresh destination resolves outside the checkout.
        """
        own = self._root / DEFAULT_CONFIG_DIR
        if inherited is None:
            self._check_fresh_target()
        else:
            InheritedConfig(inherited).seed(self._root, own)
        return own

    def _check_fresh_target(self) -> None:
        """Reject paths that resolve outside the checkout, even if absent."""
        for relative in (DEFAULT_CONFIG_DIR, *CONFIG_PATHS):
            path = self._root / relative
            if not path.resolve().is_relative_to(self._root):
                msg = "Vox config destination is outside the Git checkout"
                raise PermissionError(errno.EPERM, msg, path)
