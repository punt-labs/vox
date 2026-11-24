"""Read/write access to a single vox config file's YAML frontmatter."""

from __future__ import annotations

import errno
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Self, final

from punt_vox.config_lock import ConfigLock
from punt_vox.frontmatter_block import FrontmatterBlock

if TYPE_CHECKING:
    from collections.abc import Callable

    from punt_vox.sibling_lock import SiblingLock

logger = logging.getLogger(__name__)

__all__ = ["Frontmatter"]

# Raises to refuse a write, given the fields the file currently holds.
type FieldCheck = Callable[[dict[str, str]], None]
# Computes a write's updates from the block held under the lock (None: no file).
type FieldUpdates = Callable[[FrontmatterBlock | None], dict[str, str]]

# Expressive mood text: log its length, never the content (PY-CS-11 privacy).
_REDACTED_KEYS = frozenset({"vibe", "vibe_tags"})


@final
class Frontmatter:
    """Owns one config file and reads/writes its YAML frontmatter fields."""

    __slots__ = ("_path",)

    _path: Path

    def __new__(cls, path: Path) -> Self:
        """Create a frontmatter."""
        self = super().__new__(cls)
        self._path = path
        return self

    @property
    def path(self) -> Path:
        """Return the backing file path."""
        return self._path

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
        return self._fields(self._readable_block())

    def read_field(self, field: str, *, strict: bool = False) -> str | None:
        """Return a single frontmatter field, or ``None`` if absent.

        By default an unreadable file reads as absent. With *strict*, a file
        that exists but cannot be read or decoded raises, so a policy reader
        never takes a fault for an empty policy.

        Raises:
            OSError: If *strict* and the file exists but cannot be read.
            UnicodeDecodeError: If *strict* and the file is not UTF-8.
        """
        block = self._read_block() if strict else self._readable_block()
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
        self._replace(lambda _held: updates, check)

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

        return self._replace(updates, check)[key]

    def _replace(
        self,
        updates: FieldUpdates,
        check: FieldCheck | None,  # None: plain update.
    ) -> dict[str, str]:
        """Write ``updates(held)`` under the lock; return the fields written."""
        if check is not None:
            self._refuse_early(check)
        with self._lock().held():
            return self._write_fields(updates, check)

    def _refuse_early(self, check: FieldCheck) -> None:
        """Run a refusal before the lock file exists, so it leaves nothing."""
        block = self._read_block()
        # Every write holds the lock and its file is never removed, so with
        # no lock file yet, no write can have overlapped that read.
        if not self._lock().path.exists():
            check(self._fields(block))
            self._require_fenced(block)

    def _lock(self) -> SiblingLock:
        """Return the lock every spelling of this config path shares."""
        return ConfigLock(self._path).sibling()

    def _write_fields(
        self,
        updates: FieldUpdates,
        check: FieldCheck | None,  # None: plain update.
    ) -> dict[str, str]:
        """Read and write the config while holding its local-state lock."""
        # Read strictly on write: rebuilding an unreadable file from just
        # the updates would destroy the existing fields we need to preserve.
        held = self._read_block()
        if check is not None:
            check(self._fields(held))
            self._require_fenced(held)
        fields = updates(held)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        written = self._updated_block(held, fields)
        self._path.write_text(written.text, encoding="utf-8", newline="")
        if held is not None:  # Creating the file is not logged as an update.
            self._log_write(fields)
        return fields

    @staticmethod
    def _fields(block: FrontmatterBlock | None) -> dict[str, str]:  # None: no file.
        """Return *block*'s fields, or ``{}`` when there is no block."""
        return {} if block is None else block.fields()

    def _require_fenced(
        self,
        block: FrontmatterBlock | None,  # None: no file.
    ) -> None:
        """Refuse a checked write into a file with no closing fence.

        A key already present is no licence: without a closing fence the
        file is not frontmatter, whatever it happens to hold. An OSError,
        not a ValueError: the daemon's own file is at fault, so a wire
        handler faults instead of blaming the client, and the path rides in
        ``filename`` for the log alone, never the wire.
        """
        if block is not None and not block.fenced:
            raise OSError(errno.EBADMSG, "no closing ---", str(self._path))

    def _updated_block(
        self,
        block: FrontmatterBlock | None,  # None: no file.
        updates: dict[str, str],
    ) -> FrontmatterBlock:
        if block is None:
            return FrontmatterBlock.rendered(updates)
        if block.accepts(updates):
            return block.with_fields(updates)
        logger.warning("Malformed config (no closing ---): %s", self._path)
        return FrontmatterBlock.rendered(updates)

    def _log_write(self, updates: dict[str, str]) -> None:
        """Log one INFO summary per write, with the per-field detail at DEBUG.

        A multi-field change (a vibe set writes several) reads as a single
        line, not one per field; the mood keys log their length so the
        expressive text never lands in a durable log.
        """
        for key, value in updates.items():
            shown = f"<{len(value)} chars>" if key in _REDACTED_KEYS else repr(value)
            logger.debug("Config: set %s = %s in %s", key, shown, self._path)
        logger.info("config: updated %d field(s) in %s", len(updates), self._path.name)

    def _read_block(self) -> FrontmatterBlock | None:  # None when absent.
        """Parse the file as UTF-8, or ``None`` if absent.

        Other I/O and decode errors propagate.
        """
        try:
            text = self._path.read_text(encoding="utf-8", newline="")
        except FileNotFoundError:
            return None
        return FrontmatterBlock(text)

    def _readable_block(self) -> FrontmatterBlock | None:
        """Return the file's frontmatter, or ``None`` if absent or unreadable.

        An I/O or decode failure must degrade to defaults, not crash the hook
        subprocess (PY-EH-1). A missing file is normal and needs no warning.
        """
        try:
            return self._read_block()
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning(
                "Config unreadable, using defaults: %s (%s)", self._path, exc
            )
            return None
