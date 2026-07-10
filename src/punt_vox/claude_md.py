"""Own the punt-managed ``@``-import section of ``~/.claude/CLAUDE.md``.

Claude Code loads ``~/.claude/CLAUDE.md`` into every session and resolves any
top-level ``@path`` line as an included file. Punt tools register their usage
guides as such lines inside a shared managed section so the guides load in
every project without a per-project edit.

:class:`GlobalClaudeImports` owns ``~/.claude/CLAUDE.md``. It reconciles the
managed section by composing two collaborators: a :class:`ManagedSection`
parses the file into the user's verbatim content plus the managed import set
and renders it back deterministically (import lines sorted), and an
:class:`AtomicFile` reads the bytes verbatim and rewrites them atomically. The
reconcile writes only when the rendered text differs from what is already on
disk -- so re-running never churns the file's mtime. The vox-side artifact that
writes a usage guide and registers its own import line lives in
:mod:`punt_vox.guidance`.

**Single-writer assumption.** The managed section is shared: any punt tool may
register its own import line at its own install time. Those installs are rare
and manual, so writes are serialized in practice rather than by a lock. Each
write is atomic (temp file + ``fsync`` + ``os.replace``), so a crash mid-write
never corrupts the user's hand-authored ``CLAUDE.md``; two *concurrent*
installers could still lose one registration to a last-writer-wins race, which
is out of scope here (a lock would be overkill for manual, infrequent installs).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, final

from punt_vox.atomic_file import AtomicFile
from punt_vox.managed_section import ManagedSection

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

__all__ = ["GlobalClaudeImports"]


@final
class GlobalClaudeImports:
    """Owns the punt-managed import section of ``~/.claude/CLAUDE.md``.

    Delegates the marker grammar to a :class:`ManagedSection` and the atomic,
    byte-preserving read/write to an :class:`AtomicFile`. Import lines in the
    section are kept sorted, and the whole file is rewritten only when its text
    actually changes.
    """

    __slots__ = ("_file", "_section")

    _file: AtomicFile
    _section: ManagedSection

    def __new__(cls, path: Path) -> Self:
        self = super().__new__(cls)
        self._file = AtomicFile(path)
        self._section = ManagedSection()
        return self

    @property
    def path(self) -> Path:
        """Return the managed ``CLAUDE.md`` path."""
        return self._file.path

    def register(self, import_line: str) -> bool:
        """Add *import_line* to the managed section. Return True if written.

        *import_line* is validated at this boundary (see
        :meth:`_validate_import_line`) before it is ever written verbatim into
        the section.
        """
        self._validate_import_line(import_line)
        return self._reconcile(lambda imports: imports | {import_line})

    def prune(self, import_line: str) -> bool:
        """Remove *import_line* from the managed section. Return True if written.

        *import_line* is validated at this boundary (see
        :meth:`_validate_import_line`): a malformed line could never have been
        registered, so asking to prune one is a caller error, not a silent
        no-op.
        """
        self._validate_import_line(import_line)
        return self._reconcile(lambda imports: imports - {import_line})

    @staticmethod
    def _validate_import_line(import_line: str) -> None:
        """Raise ``ValueError`` unless *import_line* is a lone top-level ``@`` line.

        :meth:`register` and :meth:`prune` splice *import_line* into the managed
        section verbatim (one line, sorted among its peers). Today's only caller
        passes a constant, but this class is the reference reconciler other punt
        tools drive with their *own* lines, so the untrusted text is validated
        here at the boundary (PY-EH-1) rather than trusted downstream. A line
        with leading or trailing whitespace, an embedded newline, or a missing
        ``@`` prefix would otherwise inject a blank line, a second import, or
        stray markdown into the block. The rejected shapes:

        * empty / whitespace-only -- no import to register;
        * leading or trailing whitespace -- the renderer never indents an
          import, so a padded line would never match on a later prune;
        * an embedded ``\\n`` or ``\\r`` -- would splice multiple lines (or a
          second import) into the section from one call;
        * not starting with ``@`` -- Claude Code only resolves ``@path`` lines,
          so a non-``@`` line is inert markdown, not an import.
        """
        if not import_line or import_line.isspace():
            raise ValueError("import line must be non-empty")
        if "\n" in import_line or "\r" in import_line:
            raise ValueError(f"import line must be a single line: {import_line!r}")
        if import_line != import_line.strip():
            raise ValueError(
                f"import line must have no leading/trailing whitespace: {import_line!r}"
            )
        if not import_line.startswith("@"):
            raise ValueError(f"import line must begin with '@': {import_line!r}")

    def _reconcile(self, update: Callable[[frozenset[str]], frozenset[str]]) -> bool:
        """Parse, apply *update* to the import set, and write only if changed.

        The no-op-when-unchanged guard runs first: if the rendered text equals
        what is already on disk the file is never touched, so re-running never
        churns its mtime. See the module docstring for the single-writer
        assumption that lets an atomic replace stand in for a lock.
        """
        original = self._file.read()
        kept, imports = self._section.parse(original)
        new_text = self._section.render(kept, update(imports))
        if new_text == original:
            return False
        self._file.replace(new_text)
        return True
