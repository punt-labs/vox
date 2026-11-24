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
        block = self._readable_block()
        return {} if block is None else block.fields()

    def read_field(self, field: str) -> str | None:
        """Return a single frontmatter field, or ``None`` if absent/unreadable."""
        block = self._readable_block()
        return None if block is None else block.field(field)

    def write_field(self, key: str, value: str) -> None:
        """Write a single key-value pair into the frontmatter."""
        self.write_fields({key: value})

    def write_fields(
        self,
        updates: dict[str, str],
        *,
        # A plain update has no precondition; checked writes opt in.
        check: Callable[[dict[str, str]], None] | None = None,
    ) -> None:
        """Write multiple key-value pairs in a single read-write cycle.

        A check runs against the current block under the write lock. Checked
        writes never rebuild a malformed block from just the updates.
        """
        for value in updates.values():
            FrontmatterBlock.validate_value(value)
        with self._lock().held():
            self._write_fields(updates, check)

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
        check: Callable[[dict[str, str]], None] | None,  # None for plain updates.
    ) -> None:
        """Read and write the config while holding its local-state lock."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Read strictly on write: rebuilding an unreadable file from just
        # the updates would destroy the existing fields we need to preserve.
        try:
            held = self._read_block()
        except FileNotFoundError:
            if check is not None:
                check({})
            self._path.write_text(
                FrontmatterBlock.rendered(updates).text, encoding="utf-8"
            )
            return

        written = self._updated_block(held, updates, check)
        self._path.write_text(written.text, encoding="utf-8", newline="")
        self._log_write(updates)

    def _updated_block(
        self,
        block: FrontmatterBlock,
        updates: dict[str, str],
        check: Callable[[dict[str, str]], None] | None,  # Only plain writes can repair.
    ) -> FrontmatterBlock:
        if check is not None:
            check(block.fields())
        if block.accepts(updates):
            return block.with_fields(updates)
        if check is not None:
            msg = f"Malformed config (no closing ---): {self._path}"
            raise ValueError(msg)
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

    def _read_block(self) -> FrontmatterBlock:
        """Parse the file as UTF-8, propagating I/O and decode errors."""
        return FrontmatterBlock(self._path.read_text(encoding="utf-8", newline=""))

    def _readable_block(self) -> FrontmatterBlock | None:
        """Return the file's frontmatter, or ``None`` if absent or unreadable.

        An I/O or decode failure must degrade to defaults, not crash the hook
        subprocess (PY-EH-1). A missing file is normal and needs no warning.
        """
        try:
            return self._read_block()
        except FileNotFoundError:
            return None
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning(
                "Config unreadable, using defaults: %s (%s)", self._path, exc
            )
            return None
