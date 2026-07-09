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
Code does not resolve imports written inside code spans.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Self, final

from punt_vox.paths import user_state_dir

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["GlobalClaudeImports", "VoxGuidance"]


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
        """Parse, apply *update* to the import set, and write only if changed."""
        original = self._read()
        kept, imports = self._parse(original)
        new_text = self._render(kept, update(frozenset(imports)))
        if new_text == original:
            return False
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(new_text, encoding="utf-8")
        return True

    def _read(self) -> str:
        """Return the file's text, or ``""`` when it does not exist."""
        if not self._path.is_file():
            return ""
        return self._path.read_text(encoding="utf-8")

    def _parse(self, text: str) -> tuple[list[str], set[str]]:
        """Split *text* into non-managed lines and the managed import set.

        Every managed marker (well-formed pair, a lone open that never
        closes, or a stray close) is consumed, and the managed header and
        its padding are dropped. Unknown content that somehow sits between
        markers is preserved rather than destroyed. The result is that
        re-rendering always yields exactly one canonical section.
        """
        kept: list[str] = []
        imports: set[str] = set()
        inside = False
        for line in text.splitlines():
            stripped = line.strip()
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
        """Rebuild the file from non-managed lines plus one managed section."""
        while kept and not kept[-1].strip():
            kept.pop()
        body = "\n".join(kept)
        if not imports:
            return f"{body}\n" if body else ""
        section = self._build_section(imports)
        if body:
            return f"{body}\n\n{section}\n"
        return f"{section}\n"

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
