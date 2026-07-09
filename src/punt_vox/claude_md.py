"""Register vox's usage guide as a CLAUDE.md ``@``-import.

Claude Code loads ``~/.claude/CLAUDE.md`` into every session and resolves
any top-level ``@path`` line as an included file. Vox owns a usage guide at
``~/.punt-labs/vox/CLAUDE.md`` and self-registers the line
``@~/.punt-labs/vox/CLAUDE.md`` inside a shared, punt-managed section so the
guide loads in every project without a per-project edit.

Two responsibilities live here:

* :class:`GlobalClaudeImports` owns ``~/.claude/CLAUDE.md``. It splices the
  managed ``<!-- punt:mandatory-reading -->`` section deterministically
  (import lines sorted), repairs a half-written section instead of appending
  a second one, and writes only when the rendered text differs from what is
  already on disk -- so re-running never churns the file's mtime.
* :class:`VoxGuidance` owns the vox-side artifact: it writes the usage guide
  on install and deletes it plus its import line on uninstall. The installer
  rewrites the guide every run, so it is the single source of truth and can
  never drift from the running vox version.

The ``@``-lines are emitted at top level (never inside a code fence): Claude
Code does not resolve imports written inside code spans. For the same reason
the parser is fence-aware on *input* -- a ``<!-- punt:mandatory-reading -->``
marker that appears inside a fenced block (a tool documenting this feature, a
pasted README) is literal text, not a managed delimiter, and survives a
reconcile byte-for-byte.

**Single-writer assumption.** The managed section is shared: any punt tool may
register its own import line at its own install time. Those installs are rare
and manual, so writes are serialized in practice rather than by a lock. Each
write is atomic (temp file + ``fsync`` + ``os.replace``), so a crash mid-write
never corrupts the user's hand-authored ``CLAUDE.md``; two *concurrent*
installers could still lose one registration to a last-writer-wins race, which
is out of scope here (a lock would be overkill for manual, infrequent installs).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Self, final

from punt_vox.paths import user_state_dir

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["GlobalClaudeImports", "VoxGuidance"]


@final
class _Fence:
    """Tracks Markdown code-fence depth while scanning a document line by line.

    A fenced block opens on a line beginning with three or more backticks or
    tildes and closes on a bare run of the same fence character. Inside a
    fence every line is literal text, so the reconciler must not read a
    managed marker there. Feed each line's stripped form in document order;
    :attr:`inside` reports whether the following lines are fenced content.
    """

    __slots__ = ("_char",)

    # The fence character (``` `` ``` `` or ``~``) of the open block, or ``None``
    # when not inside a fence. ``None`` is the documented "no open fence" state,
    # not a failure to produce a value.
    _char: str | None

    def __new__(cls) -> Self:
        self = super().__new__(cls)
        self._char = None
        return self

    @property
    def inside(self) -> bool:
        """Return whether the scanner is currently within a fenced block."""
        return self._char is not None

    def feed(self, stripped: str) -> bool:
        """Update state from *stripped*; return True if it is a fence delimiter.

        A delimiter is the opening fence when outside a block, or the matching
        closing fence when inside one. Content lines return False.
        """
        if self._char is None:
            opener = self._opener_char(stripped)
            if opener is None:
                return False
            self._char = opener
            return True
        if self._closes(stripped):
            self._char = None
            return True
        return False

    @staticmethod
    def _opener_char(stripped: str) -> str | None:
        """Return the fence character if *stripped* opens a fence, else None.

        ``None`` is the contract for "not a fence line" (absence), matching the
        ``dict.get`` idiom -- it is not a value the caller failed to produce.
        """
        for char in ("`", "~"):
            if stripped.startswith(char * 3):
                return char
        return None

    def _closes(self, stripped: str) -> bool:
        """Return whether *stripped* is a bare closing fence for the open char.

        A closing fence is a run of the open character (three or more) with no
        trailing info string; ```` ```python ```` opens but never closes.
        """
        char = self._char
        if char is None:
            return False
        return stripped.startswith(char * 3) and stripped == char * len(stripped)


@final
class GlobalClaudeImports:
    """Owns the punt-managed import section of ``~/.claude/CLAUDE.md``.

    The section is delimited by shared markers so a future punt-owned
    multi-tool reconcile supersedes this one cleanly. All import lines in
    the section are kept sorted, and the whole file is rewritten only when
    its text actually changes.
    """

    __slots__ = ("_path",)

    _path: Path

    # Shared markers -- not vox-specific -- so any punt tool can own the
    # same section and a future reconcile can collapse them together.
    _OPEN = "<!-- punt:mandatory-reading -->"
    _CLOSE = "<!-- /punt:mandatory-reading -->"
    # Names the auto-loaded reality: the imported docs load with no action
    # from the reader, so the header must not read like a manual instruction.
    _HEADER = "## Tool Guidance (auto-loaded)"

    def __new__(cls, path: Path) -> Self:
        self = super().__new__(cls)
        self._path = path
        return self

    @property
    def path(self) -> Path:
        """Return the managed ``CLAUDE.md`` path."""
        return self._path

    def register(self, import_line: str) -> bool:
        """Add *import_line* to the managed section. Return True if written."""
        return self._reconcile(lambda imports: imports | {import_line})

    def prune(self, import_line: str) -> bool:
        """Remove *import_line* from the managed section. Return True if written."""
        return self._reconcile(lambda imports: imports - {import_line})

    def _reconcile(self, update: Callable[[frozenset[str]], frozenset[str]]) -> bool:
        """Parse, apply *update* to the import set, and write only if changed.

        The no-op-when-unchanged guard runs first: if the rendered text equals
        what is already on disk the file is never touched, so re-running never
        churns its mtime. See the module docstring for the single-writer
        assumption that lets an atomic replace stand in for a lock.
        """
        original = self._read()
        kept, imports = self._parse(original)
        new_text = self._render(kept, update(frozenset(imports)))
        if new_text == original:
            return False
        self._write_atomic(new_text)
        return True

    def _write_atomic(self, text: str) -> None:
        """Replace the file's contents atomically.

        Write *text* to a temporary file in the target's own directory, flush
        and ``fsync`` it, then ``Path.replace`` it over the target.
        ``Path.replace`` wraps ``os.replace`` -- an atomic rename on POSIX
        (macOS and Linux, the only supported platforms) -- so an interrupted
        write (SIGKILL, power loss) leaves the original ``CLAUDE.md`` untouched
        rather than truncated: the user's hand-authored content is never at
        risk, only the pending replacement. The temporary file is unlinked if
        any step fails before the rename.
        """
        directory = self._path.parent
        directory.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=directory, prefix=".claude-md-", suffix=".tmp"
        )
        tmp = Path(tmp_name)
        replaced = False
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            tmp.replace(self._path)
            replaced = True
        finally:
            if not replaced:
                tmp.unlink(missing_ok=True)

    def _read(self) -> str:
        """Return the file's text, or ``""`` when it does not exist."""
        if not self._path.is_file():
            return ""
        return self._path.read_text(encoding="utf-8")

    def _parse(self, text: str) -> tuple[list[str], set[str]]:
        """Split *text* into non-managed lines and the managed import set.

        Non-managed lines keep their original endings
        (``splitlines(keepends=True)``), so ``"".join(kept)`` reproduces the
        user's content byte-for-byte -- trailing blank lines and all.

        Every managed marker (well-formed pair, a lone open that never
        closes, or a stray close) is consumed, and the managed header and
        its padding are dropped. Unknown content that somehow sits between
        markers is preserved rather than destroyed. The result is that
        re-rendering always yields exactly one canonical section.

        Parsing is fence-aware: any line inside a Markdown code fence (a
        ```` ``` ```` or ``~~~`` block) is literal text and is preserved
        byte-for-byte, so a ``<!-- punt:mandatory-reading -->`` marker that
        someone documented inside a fenced block is never mistaken for a real
        delimiter and never eaten.
        """
        kept: list[str] = []
        imports: set[str] = set()
        inside = False
        fence = _Fence()
        for line in text.splitlines(keepends=True):
            stripped = line.strip()
            was_fenced = fence.inside
            if fence.feed(stripped) or was_fenced:
                # Fence delimiter or fenced content: literal, never a marker.
                kept.append(line)
                continue
            if stripped == self._OPEN:
                inside = True
                continue
            if stripped == self._CLOSE:
                inside = False
                continue
            if inside:
                if stripped.startswith("@"):
                    imports.add(stripped)
                elif stripped and stripped != self._HEADER:
                    kept.append(line)
                continue
            kept.append(line)
        return kept, imports

    def _render(self, kept: list[str], imports: frozenset[str]) -> str:
        """Rebuild the file from the verbatim non-managed content plus one section.

        *kept* holds the user's lines verbatim (see :meth:`_parse`), so content
        outside the managed section is preserved byte-for-byte. When no import
        remains the result is exactly the user's content -- uninstalling a file
        that had no section before install restores it byte-for-byte.
        """
        body = "".join(kept)
        if not imports:
            return body
        section = self._build_section(imports)
        if not body:
            return f"{section}\n"
        # Anchor the OPEN marker to its own line without mutating the user's
        # trailing whitespace: no separator when the body already ends in a
        # newline (trailing blank lines included), one added when it does not.
        # No blank line is injected -- anything beyond OPEN..CLOSE would survive
        # the prune and break the byte-for-byte round-trip.
        separator = "" if body.endswith("\n") else "\n"
        return f"{body}{separator}{section}\n"

    def _build_section(self, imports: frozenset[str]) -> str:
        """Render the canonical managed section with sorted import lines."""
        lines = [self._OPEN, self._HEADER, "", *sorted(imports), self._CLOSE]
        return "\n".join(lines)


@final
class VoxGuidance:
    """Owns vox's usage guide and its registration in ``~/.claude/CLAUDE.md``.

    The guide is written to ``~/.punt-labs/vox/CLAUDE.md`` -- distinct from
    the repo-local ``.punt-labs/vox/vox.md`` config, so there is no
    collision. The installer rewrites the guide every run; uninstall deletes
    it and prunes its import line.
    """

    __slots__ = ("_doc_path", "_global", "_import_line")

    _doc_path: Path
    _global: GlobalClaudeImports
    _import_line: str

    _ASSET_NAME = "global-guidance.md"

    def __new__(
        cls, doc_path: Path, global_imports: GlobalClaudeImports, import_line: str
    ) -> Self:
        self = super().__new__(cls)
        self._doc_path = doc_path
        self._global = global_imports
        self._import_line = import_line
        return self

    @classmethod
    def for_current_user(cls) -> Self:
        """Wire the real per-user paths for the running install."""
        home = Path.home()
        doc_path = user_state_dir() / "CLAUDE.md"
        import_line = "@~/" + doc_path.relative_to(home).as_posix()
        global_path = home / ".claude" / "CLAUDE.md"
        return cls(doc_path, GlobalClaudeImports(global_path), import_line)

    @property
    def doc_path(self) -> Path:
        """Return the path of the vox usage guide."""
        return self._doc_path

    @property
    def import_line(self) -> str:
        """Return the ``@``-import line registered in the global CLAUDE.md."""
        return self._import_line

    def install(self) -> str:
        """Write the guide and register its import. Return a status message."""
        self._doc_path.parent.mkdir(parents=True, exist_ok=True)
        self._doc_path.write_text(self._load_doc(), encoding="utf-8")
        wrote = self._global.register(self._import_line)
        state = "registered" if wrote else "already registered"
        return (
            f"vox usage guide written to {self._doc_path}; "
            f"import {state} in {self._global.path}"
        )

    def uninstall(self) -> str:
        """Delete the guide and prune its import. Return a status message."""
        if self._doc_path.is_file():
            self._doc_path.unlink()
        self._global.prune(self._import_line)
        return (
            f"vox usage guide removed ({self._doc_path}); "
            f"import pruned from {self._global.path}"
        )

    def _load_doc(self) -> str:
        """Read the usage guide bundled beside this package."""
        asset = Path(__file__).resolve().parent / "assets" / self._ASSET_NAME
        return asset.read_text(encoding="utf-8")
