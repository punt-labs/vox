"""Append missing ignore rules without replacing any host-authored rules."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Self, final

from punt_vox.atomic_file import AtomicFile
from punt_vox.sibling_lock import SiblingLock

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["VoxGitignore"]

_LOCAL_IGNORES = (
    ".punt-labs/vox/vox.local.md",
    ".punt-labs/local/",
    "/.CLAUDE.md.punt-import.lock",
    "/.claude/.settings.json.punt-import.lock",
)


@final
class VoxGitignore:
    """Keep vox's local state and locks out of a host repo's changes.

    Add all missing rules under one lock and in one atomic write.
    """

    __slots__ = ("_file", "_lock")

    _file: AtomicFile
    _lock: SiblingLock

    def __new__(cls, repo: Path) -> Self:
        """Create a gitignore writer."""
        self = super().__new__(cls)
        host = repo / ".gitignore"
        self._file = AtomicFile(host)
        self._lock = SiblingLock(
            host, lock_path=repo / ".punt-labs" / "local" / "vox" / "gitignore.lock"
        )
        return self

    def ensure(self) -> bool:
        """Append missing rules. Return ``True`` if the file changed.

        A missing ``.gitignore`` is created. Appended lines use the host's
        first line terminator and a missing final terminator is added first,
        so a rule never fuses onto the user's last rule.

        Raises:
            ValueError: If the host is a symlink. Git does not read a symlinked
                ``.gitignore``, and writing through it would edit a file
                outside the repo.
        """
        with self._lock.held():
            if self._file.path.is_symlink():
                msg = (
                    f"{self._file.path} is a symlink; git ignores it, vox won't edit it"
                )
                raise ValueError(msg)
            text = self._file.read()
            missing = self._missing_entries(text)
            if not missing:
                return False
            eol = self._eol(text)
            if text and not text.endswith("\n"):
                # Finish a trailing CR without writing a second one.
                text += eol.removeprefix(text[-1])
            self._file.replace(f"{text}{eol.join(missing)}{eol}")
            return True

    @staticmethod
    def _missing_entries(text: str) -> tuple[str, ...]:
        """Recognize listed rules with either spelling and git's trailing spaces."""
        listed = {
            line.removesuffix("\r").rstrip(" ").removeprefix("/")
            for line in text.split("\n")
        }
        return tuple(
            entry for entry in _LOCAL_IGNORES if entry.removeprefix("/") not in listed
        )

    @staticmethod
    def _eol(text: str) -> str:
        """Use CRLF only when the host's first newline uses CRLF."""
        return match[0] if (match := re.search(r"\r?\n", text)) else "\n"
