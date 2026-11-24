"""Own one exclusion line inside a host repo's ``.gitignore``.

``vox enable`` deposits files under ``.punt-labs/vox/`` in any repo, and one of
them -- ``vox.local.md`` (:data:`~punt_vox.config.EPHEMERAL_FILENAME`) -- holds
machine-local session state (vibe, mood, log level) that must never be
committed. Vox's own repo ignores it by hand; every other repo depends on
``enable`` adding the line, or the first commit after a ``vox vibe`` sweeps the
file into history.

:class:`GitignoreEntry` appends the line when no equivalent line is present and
leaves every other byte of the host untouched. It is additive only: ``disable``
never removes it, because a user may rely on the line independently of vox.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, final

from punt_vox.atomic_file import AtomicFile
from punt_vox.sibling_lock import SiblingLock

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["GitignoreEntry"]


@final
class GitignoreEntry:
    """Ensures one repo-relative path is listed in a host ``.gitignore``.

    Bind the host file, the path, and a lock file at construction; the path is
    validated once (:meth:`_validate`) because it is spliced into the host
    verbatim. :meth:`ensure` holds the lock across its read-modify-write, so
    concurrent ``enable`` runs never lose a line. The caller places the lock
    (vox uses its ``.punt-labs/local/vox/`` lock zone) so ``enable`` leaves no
    new lock file in the repo root.
    """

    __slots__ = ("_entry", "_file", "_lock")

    _entry: str
    _file: AtomicFile
    _lock: SiblingLock

    def __new__(cls, host_path: Path, entry: str, *, lock_path: Path) -> Self:
        """Create a gitignore entry."""
        cls._validate(entry)
        self = super().__new__(cls)
        self._entry = entry
        self._file = AtomicFile(host_path)
        self._lock = SiblingLock(host_path, lock_path=lock_path)
        return self

    @property
    def entry(self) -> str:
        """Return the path this instance keeps ignored."""
        return self._entry

    def is_present(self) -> bool:
        """Return whether the host already lists the entry (bare or ``/``-anchored)."""
        return self._lists_entry(self._file.read())

    def ensure(self) -> bool:
        """Append the entry if absent. Return ``True`` if the file changed.

        A missing ``.gitignore`` is created. The appended line uses the host's
        first line terminator and a missing final terminator is added first,
        so the entry never fuses onto the user's last rule.
        """
        with self._lock.held():
            text = self._file.read()
            if self._lists_entry(text):
                return False
            eol = self._eol(text)
            if text and not text.endswith(("\n", "\r")):
                text += eol
            self._file.replace(f"{text}{self._entry}{eol}")
            return True

    def _lists_entry(self, text: str) -> bool:
        accepted = {self._entry, f"/{self._entry}"}
        return any(line.strip() in accepted for line in text.splitlines())

    @staticmethod
    def _eol(text: str) -> str:
        r"""Return the host's first line terminator: CRLF, lone CR, or LF."""
        cr = text.find("\r")
        lf = text.find("\n")
        if cr != -1 and (lf == -1 or cr < lf):
            return "\r\n" if text[cr + 1 : cr + 2] == "\n" else "\r"
        return "\n"

    @staticmethod
    def _validate(entry: str) -> None:
        """Raise ``ValueError`` unless *entry* is one plain repo-relative path.

        A blank, padded, multi-line, absolute, comment, or negated value would
        write a rule other than the one intended.
        """
        if (
            not entry
            or entry != entry.strip()
            or any(c in entry for c in "\r\n")
            or entry[0] in "/#!"
        ):
            msg = f"gitignore entry must be one relative path: {entry!r}"
            raise ValueError(msg)
