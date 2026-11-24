"""Read/write access to a single vox config file's YAML frontmatter."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Self, final

from punt_vox.config_file import ConfigFile
from punt_vox.config_lock import ConfigLock
from punt_vox.frontmatter_block import FrontmatterBlock

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from punt_vox.frontmatter_edit import FieldCheck, FrontmatterEdit

logger = logging.getLogger(__name__)

__all__ = ["Frontmatter"]

# Expressive mood text: log its length, never the content (PY-CS-11 privacy).
_REDACTED_KEYS = frozenset({"vibe", "vibe_tags"})


@final
class Frontmatter:
    """Owns one config file and reads/writes its YAML frontmatter fields."""

    __slots__ = ("_file",)

    _file: ConfigFile

    def __new__(cls, path: Path) -> Self:
        """Create a frontmatter."""
        self = super().__new__(cls)
        self._file = ConfigFile(path)
        return self

    @property
    def path(self) -> Path:
        """Return the backing file path."""
        return self._file.path

    @staticmethod
    def validate_value(value: str) -> None:
        """Reject values that would corrupt the ``key: "<value>"`` round-trip.

        Raises :class:`~punt_vox.types_errors.ConfigValueError`. Kept on this
        class because the config layer validates before routing a batch to
        two files, and asks the writer, not the format, whether a value fits.
        """
        FrontmatterBlock.validate_value(value)

    def read_fields(self) -> dict[str, str]:
        """Return all non-empty frontmatter fields, or ``{}`` if unreadable."""
        block = self._file.readable_block()
        return {} if block is None else block.fields()

    def read_field(self, field: str, *, strict: bool = False) -> str | None:
        """Return a single frontmatter field, or ``None`` if absent.

        By default an unreadable file reads as absent. With *strict*, a file
        that exists but cannot be read or decoded raises, and so does one
        with an opening fence and no closing one: every edit of that file
        faults, so a policy read from it would list entries no edit could
        ever touch. A missing file is a valid, empty policy either way.

        Raises:
            OSError: If *strict* and the file exists but cannot be read, or
                has no closing fence.
            UnicodeDecodeError: If *strict* and the file is not UTF-8.
        """
        block = self._file.fenced_block() if strict else self._file.readable_block()
        return None if block is None else block.field(field)

    def write_field(self, key: str, value: str) -> None:
        """Write a single key-value pair into the frontmatter."""
        self.write_fields({key: value})

    def write_fields(
        self,
        updates: dict[str, str],
        *,
        # A plain update has no precondition; checked writes opt in.
        check: FieldCheck | None = None,
    ) -> None:
        """Write multiple key-value pairs in a single read-write cycle.

        Every value is validated up front, so the serialization invariant
        is enforced by the class that serializes -- a caller bypassing
        ``ConfigStore`` still cannot corrupt the frontmatter (PY-EH-1).

        A check runs against the current block under the write lock, and a
        checked write refuses a file without a closing fence, even one that
        already holds every key, instead of editing or rebuilding it. That
        refusal is an ``OSError`` naming the file: the daemon's own config is
        at fault, not the caller's request. Until the lock file exists, the
        check also runs before the lock is taken, so a refused write leaves
        nothing on disk.

        Raises:
            ConfigValueError: If a value cannot be stored.
            ValueError: If *check* refuses.
            OSError: If the file exists but cannot be read, cannot be written,
                or (on a checked write) has no closing fence.
            UnicodeDecodeError: If the file is not UTF-8.
        """
        for value in updates.values():
            FrontmatterBlock.validate_value(value)
        self._replace(self._file.edit(lambda _held: updates, check))

    def update_field(
        self,
        key: str,
        transform: Callable[[str | None], str],
        *,
        check: FieldCheck | None = None,  # None: plain update.
    ) -> str:
        """Replace *key* with ``transform(current)`` in one locked read-modify-write.

        The file lock is held from the read through the write, so a writer
        that lands between them waits instead of being overwritten. *current*
        is ``None`` when the file or the key is absent. The new value is
        returned. A ``ValueError`` from *transform* leaves the file untouched.
        A *check* has the same meaning as on :meth:`write_fields`.

        Raises:
            ConfigValueError: If the new value cannot be stored.
            ValueError: If *check* refuses.
            OSError: If the file exists but cannot be read, cannot be written,
                or (on a checked write) has no closing fence.
            UnicodeDecodeError: If the file is not UTF-8.
        """

        def updates(held: FrontmatterBlock | None) -> dict[str, str]:
            value = transform(None if held is None else held.field(key))
            FrontmatterBlock.validate_value(value)
            return {key: value}

        return self._replace(self._file.edit(updates, check))[key]

    def _replace(self, edit: FrontmatterEdit) -> dict[str, str]:
        """Land *edit* under the lock; return the fields it wrote.

        A checked edit is admitted once before the lock file exists, so a
        refusal leaves nothing on disk. The early read comes before the
        lock-file test: every write holds the lock and its file is never
        removed, so a read that finds no lock file afterwards overlapped no
        write, while a writer that started during the read leaves the check
        to the locked pass.
        """
        # The lock every spelling of this config path shares.
        lock = ConfigLock(self._file.path).sibling()
        if edit.checked:
            held = self._file.block()
            if not lock.path.exists():
                edit.admit(held)
        with lock.held():
            return self._write_fields(edit)

    def _write_fields(self, edit: FrontmatterEdit) -> dict[str, str]:
        """Read and write the config while holding its local-state lock."""
        # Read strictly on write: rebuilding an unreadable file from just
        # the updates would destroy the existing fields we need to preserve.
        held = self._file.block()
        edit.admit(held)
        fields = edit.fields(held)
        self._file.replace(edit.block(held, fields))
        if held is not None:  # Creating the file is not logged as an update.
            self._log_write(fields)
        return fields

    def _log_write(self, updates: dict[str, str]) -> None:
        """Log one INFO summary per write, with the per-field detail at DEBUG.

        A multi-field change (a vibe set writes several) reads as a single
        line, not one per field; the mood keys log their length so the
        expressive text never lands in a durable log.
        """
        path = self._file.path
        for key, value in updates.items():
            shown = f"<{len(value)} chars>" if key in _REDACTED_KEYS else repr(value)
            logger.debug("Config: set %s = %s in %s", key, shown, path)
        logger.info("config: updated %d field(s) in %s", len(updates), path.name)
