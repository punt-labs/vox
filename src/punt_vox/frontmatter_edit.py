"""One edit to a config file's frontmatter: what it changes, and what admits it."""

from __future__ import annotations

import errno
import logging
from typing import TYPE_CHECKING, Self, final

from punt_vox.frontmatter_block import FrontmatterBlock

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["FieldCheck", "FieldUpdates", "FrontmatterEdit"]

# Raises to refuse a write, given the fields the file currently holds.
type FieldCheck = Callable[[dict[str, str]], None]
# Computes a write's updates from the block held under the lock (None: no file).
type FieldUpdates = Callable[[FrontmatterBlock | None], dict[str, str]]


@final
class FrontmatterEdit:
    """A write's updates and its precondition, applied to the block a file holds.

    A plain edit has no precondition. A checked edit runs its check against
    the fields the file holds and refuses a file with no closing fence, even
    one that already holds every key: without the fence the file is not
    frontmatter, whatever it happens to hold.
    """

    __slots__ = ("_check", "_path", "_updates")

    _path: Path
    _updates: FieldUpdates
    _check: FieldCheck | None  # None: a plain edit, admitted without a check.

    def __new__(
        cls,
        path: Path,
        updates: FieldUpdates,
        check: FieldCheck | None,  # None: plain edit.
    ) -> Self:
        """Bind the file's path, the updates and the optional precondition."""
        self = super().__new__(cls)
        self._path = path
        self._updates = updates
        self._check = check
        return self

    @property
    def checked(self) -> bool:
        """Return whether this edit carries a precondition."""
        return self._check is not None

    def admit(self, held: FrontmatterBlock | None) -> None:  # None: no file.
        """Run the precondition against *held*; a plain edit is admitted as is.

        The fence refusal is an ``OSError``, not a ``ValueError``: the
        daemon's own file is at fault, so a wire handler faults instead of
        blaming the client, and the path rides in ``filename`` for the log
        alone, never the wire.

        Raises:
            ValueError: If the check refuses.
            OSError: If the check passed but *held* has no closing fence.
        """
        if self._check is None:
            return
        self._check({} if held is None else held.fields())
        if held is not None and not held.fenced:
            raise OSError(errno.EBADMSG, "no closing ---", str(self._path))

    def fields(self, held: FrontmatterBlock | None) -> dict[str, str]:  # None: no file.
        """Return the fields this edit writes, computed from *held*."""
        return self._updates(held)

    def block(
        self,
        held: FrontmatterBlock | None,  # None: no file.
        fields: dict[str, str],
    ) -> FrontmatterBlock:
        """Return *held* with *fields* applied, or a fresh block when it cannot.

        A file with no closing fence and none of the keys is rebuilt from the
        fields alone, with a warning naming it: a plain edit never lands a
        field below prose where no reader would find it.
        """
        if held is None:
            return FrontmatterBlock.rendered(fields)
        if held.accepts(fields):
            return held.with_fields(fields)
        logger.warning("Malformed config (no closing ---): %s", self._path)
        return FrontmatterBlock.rendered(fields)
