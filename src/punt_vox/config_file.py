"""One config file on disk: read it as a frontmatter block, replace it whole."""

from __future__ import annotations

import errno
import logging
import os
from typing import TYPE_CHECKING, Self, final

from punt_vox.atomic_file import AtomicFile
from punt_vox.frontmatter_block import FrontmatterBlock
from punt_vox.frontmatter_edit import FrontmatterEdit

if TYPE_CHECKING:
    from pathlib import Path

    from punt_vox.frontmatter_edit import FieldCheck, FieldUpdates

logger = logging.getLogger(__name__)

__all__ = ["ConfigFile"]


@final
class ConfigFile:
    """The bytes of one config file, and nothing about what they mean.

    Three reads: a strict one for a writer, which must not mistake an
    unreadable file for an empty one; a fenced one for a policy reader,
    which also refuses a file no edit could land in; and a lenient one for a
    hook subprocess, which must not crash on either. One write, which lands
    whole or not at all.
    """

    __slots__ = ("_path",)

    _path: Path

    def __new__(cls, path: Path) -> Self:
        """Bind the file's path; nothing is read until asked."""
        self = super().__new__(cls)
        self._path = path
        return self

    @property
    def path(self) -> Path:
        """Return the file's path."""
        return self._path

    def edit(
        self,
        updates: FieldUpdates,
        check: FieldCheck | None,  # None: plain edit.
    ) -> FrontmatterEdit:
        """Return an edit of this file, named after it in any refusal or warning."""
        return FrontmatterEdit(self._path, updates, check)

    def block(self) -> FrontmatterBlock | None:  # None when absent.
        """Parse the file as UTF-8, or ``None`` if absent.

        Raises:
            OSError: If the file exists but cannot be read.
            UnicodeDecodeError: If the file is not UTF-8.
        """
        try:
            text = self._path.read_text(encoding="utf-8", newline="")
        except FileNotFoundError:
            return None
        return FrontmatterBlock(text)

    def fenced_block(self) -> FrontmatterBlock | None:  # None when absent.
        """Parse the file strictly, refusing one with no closing fence.

        Every edit of such a file faults, so a policy read from it would list
        entries no edit could ever touch. The refusal is the same ``OSError``
        the writer raises, naming the file for the log alone.

        Raises:
            OSError: If the file exists but cannot be read, or has an opening
                fence and no closing one.
            UnicodeDecodeError: If the file is not UTF-8.
        """
        block = self.block()
        if block is not None and not block.fenced:
            raise OSError(errno.EBADMSG, "no closing ---", str(self._path))
        return block

    def readable_block(self) -> FrontmatterBlock | None:
        """Return the file's frontmatter, or ``None`` if absent or unreadable.

        An I/O or decode failure must degrade to defaults, not crash the hook
        subprocess (PY-EH-1). A missing file is normal and needs no warning.
        """
        try:
            return self.block()
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning(
                "Config unreadable, using defaults: %s (%s)", self._path, exc
            )
            return None

    def replace(self, block: FrontmatterBlock) -> None:
        """Land *block* as the whole file, or leave the file as it was.

        A sibling temp file renamed over the config: a write that runs out of
        space partway leaves every setting the file held, never a truncated
        file with nothing in it. An existing file is first opened for
        writing, because the rename needs only the directory and would
        otherwise replace a file its owner made read-only.

        Raises:
            OSError: If the file or its directory cannot be written.
        """
        if self._path.exists():
            os.close(os.open(self._path, os.O_WRONLY | os.O_CLOEXEC))
        AtomicFile(self._path).replace(block.text)
