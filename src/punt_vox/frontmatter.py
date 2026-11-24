"""Read/write access to a single vox config file's YAML frontmatter."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Self, final

from punt_vox.frontmatter_block import FrontmatterBlock
from punt_vox.paths import run_dir
from punt_vox.sibling_lock import SiblingLock

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

__all__ = ["Frontmatter"]

# Raises to refuse a write, given the fields the file currently holds.
type FieldCheck = Callable[[dict[str, str]], None]

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

        A check runs against the current block under the write lock, and
        checked writes never rebuild a malformed block from just the updates.
        Until the lock file exists, the check also runs before the lock is
        taken, so a refused write leaves nothing on disk.
        """
        for value in updates.values():
            FrontmatterBlock.validate_value(value)
        if check is not None:
            self._refuse_early(updates, check)
        with self._lock().held():
            self._write_fields(updates, check)

    def _refuse_early(self, updates: dict[str, str], check: FieldCheck) -> None:
        """Run a refusal before the lock file exists, so it leaves nothing."""
        block = self._read_block()
        # Every write holds the lock and its file is never removed, so with
        # no lock file yet, no write can have overlapped that read.
        if not self._lock().path.exists():
            check(self._fields(block))
            self._require_fenced(block)

    def _lock(self) -> SiblingLock:
        """Share a local-state lock across every spelling of the config path."""
        # Read-only .punt-labs requires a pre-provisioned, writable local lock zone.
        target = self._path.resolve()
        directory = target.parent
        if directory.name == "vox" and directory.parent.name == ".punt-labs":
            lock_path = directory.parent / "local" / "vox" / f"{target.name}.lock"
        else:
            digest = hashlib.sha256(str(target).encode()).hexdigest()
            lock_path = run_dir() / "config-locks" / digest
        return SiblingLock(target, lock_path=lock_path, mode=0o666)

    def _write_fields(
        self,
        updates: dict[str, str],
        check: FieldCheck | None,  # None: plain update.
    ) -> None:
        """Read and write the config while holding its local-state lock."""
        # Read strictly on write: rebuilding an unreadable file from just
        # the updates would destroy the existing fields we need to preserve.
        held = self._read_block()
        if check is not None:
            check(self._fields(held))
            self._require_fenced(held)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        written = self._updated_block(held, updates)
        self._path.write_text(written.text, encoding="utf-8", newline="")
        if held is not None:  # Creating the file is not logged as an update.
            self._log_write(updates)

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
        file is not frontmatter, whatever it happens to hold.
        """
        if block is not None and not block.fenced:
            msg = f"Malformed config (no closing ---): {self._path}"
            raise ValueError(msg)

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
