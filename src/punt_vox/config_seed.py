"""Give a nested repo its own vox config, seeded from the one it inherits."""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Self, final

from punt_vox.enablement_state import PersonalTargetGuard
from punt_vox.gitignore_entry import VoxGitignore

__all__ = ["InheritedConfig"]

logger = logging.getLogger(__name__)

_CONFIG_FILES = ("vox.md", "vox.local.md")


@final
class InheritedConfig:
    """The optional config directory a repo reads from above its own root.

    A repo with no ``.punt-labs/vox/`` config of its own reads its
    workspace's.  A setting write from inside it must not edit that shared
    config, so :meth:`seed` copies it into the repo, after which only the
    written setting differs and the repo reads its own copy.
    """

    __slots__ = ("_source",)

    _source: Path | None  # None for ignore-only setup with no config to copy.

    def __new__(cls, source: Path | None) -> Self:
        """Wrap the inherited config directory *source*, if one exists."""
        self = super().__new__(cls)
        self._source = source
        return self

    def seed(self, repo: Path, own: Path) -> None:
        """Ignore local state and seed *own* with any inherited files.

        The copies are staged in a sibling directory and renamed into place,
        so an interrupted or failed seed never leaves *own* with only some of
        them, nor a stray staging directory.
        When *own* already exists without config (say, only the ``enabled``
        marker) each copy is linked in on its own, never replacing a file a
        concurrent writer created first.  The repo's ``.gitignore`` gains
        vox's local-state rules before any copy lands, so the copied
        ``vox.local.md`` is never committed.
        """
        self._ignore_local_state(repo)
        if self._source is None:
            return
        own.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=".vox-seed-", dir=own.parent, ignore_cleanup_errors=True
        ) as directory:
            staging = Path(directory)
            for name in _CONFIG_FILES:
                with contextlib.suppress(FileNotFoundError):
                    shutil.copy2(self._source / name, staging / name)
            self._move_into(staging, own)

    @staticmethod
    def _ignore_local_state(repo: Path) -> None:
        """Add vox's local-state rules to *repo*'s ``.gitignore`` if needed."""
        local_cfg = repo / ".punt-labs" / "vox" / "vox.local.md"
        if PersonalTargetGuard(repo).is_ignored(local_cfg):
            return
        try:
            VoxGitignore(repo).ensure()
        except ValueError as exc:
            logger.warning("vox.local.md left trackable: %s", exc)

    @staticmethod
    def _move_into(staging: Path, own: Path) -> None:
        """Rename *staging* to *own*, or link its files into an existing *own*."""
        with contextlib.suppress(OSError):
            staging.rename(own)
            return
        for copy in staging.iterdir():
            with contextlib.suppress(FileExistsError):
                os.link(copy, own / copy.name)
