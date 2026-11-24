"""Give a nested repo its own vox config, seeded from the one it inherits."""

from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
from pathlib import Path
from typing import Self, final

from punt_vox.gitignore_entry import VoxGitignore

__all__ = ["InheritedConfig"]

_CONFIG_FILES = ("vox.md", "vox.local.md")


@final
class InheritedConfig:
    """A config directory a repo reads from above its own root.

    A repo with no ``.punt-labs/vox/`` config of its own reads its
    workspace's.  A setting write from inside it must not edit that shared
    config, so :meth:`seed` copies it into the repo, after which only the
    written setting differs and the repo reads its own copy.
    """

    __slots__ = ("_source",)

    _source: Path

    def __new__(cls, source: Path) -> Self:
        """Wrap the inherited config directory *source*."""
        self = super().__new__(cls)
        self._source = source
        return self

    def seed(self, repo: Path, own: Path) -> None:
        """Create *own* (in *repo*) holding copies of the inherited files.

        The copies are staged in a sibling directory and renamed into place,
        so an interrupted or failed seed never leaves *own* with only some of
        them, nor a stray staging directory.
        When *own* already exists without config (say, only the ``enabled``
        marker) each copy is linked in on its own, never replacing a file a
        concurrent writer created first.  The repo's ``.gitignore`` gains
        vox's local-state rules so the copied ``vox.local.md`` is never
        committed.
        """
        own.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".vox-seed-", dir=own.parent))
        try:
            for name in _CONFIG_FILES:
                with contextlib.suppress(FileNotFoundError):
                    shutil.copy2(self._source / name, staging / name)
            self._move_into(staging, own)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        # Git does not read a symlinked .gitignore, which VoxGitignore refuses.
        with contextlib.suppress(ValueError):
            VoxGitignore(repo).ensure()

    @staticmethod
    def _move_into(staging: Path, own: Path) -> None:
        """Rename *staging* to *own*, or link its files into an existing *own*."""
        try:
            staging.rename(own)
        except OSError:
            for copy in staging.iterdir():
                with contextlib.suppress(FileExistsError):
                    os.link(copy, own / copy.name)
