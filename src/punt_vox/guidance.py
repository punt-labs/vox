"""Install vox's usage guide and register it as a global CLAUDE.md import.

:class:`VoxGuidance` owns the vox-side artifact: it writes the usage guide to
``~/.punt-labs/vox/CLAUDE.md`` and registers the line
``@~/.punt-labs/vox/CLAUDE.md`` in the user's ``~/.claude/CLAUDE.md`` via
:class:`~punt_vox.claude_md.GlobalClaudeImports`, so the guide loads in every
Claude Code session without a per-project edit. The installer rewrites the
guide every run, so it is the single source of truth and can never drift from
the running vox version; uninstall deletes the guide and prunes its import.
"""

from __future__ import annotations

from pathlib import Path
from typing import Self, final

from punt_vox.claude_md import GlobalClaudeImports
from punt_vox.paths import user_state_dir

__all__ = ["VoxGuidance"]


@final
class VoxGuidance:
    """Owns vox's usage guide and its registration in ``~/.claude/CLAUDE.md``.

    The guide is written to ``~/.punt-labs/vox/CLAUDE.md`` -- distinct from the
    repo-local ``.punt-labs/vox/vox.md`` config, so there is no collision.
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
        """Delete the guide and prune its import. Return a status message.

        The two teardown steps run independently: a failing ``unlink`` (a
        permissions error, or a race that already removed the doc) must not
        skip the prune, or the managed ``@``-import would be orphaned --
        pointing at a now-deleted guide. Both are attempted before any error
        is re-raised, so a partial failure still tears down as much as it can.
        """
        errors: list[OSError] = []
        try:
            if self._doc_path.is_file():
                self._doc_path.unlink()
        except OSError as exc:
            errors.append(exc)
        try:
            self._global.prune(self._import_line)
        except OSError as exc:
            errors.append(exc)
        if errors:
            raise errors[0]
        return (
            f"vox usage guide removed ({self._doc_path}); "
            f"import pruned from {self._global.path}"
        )

    def _load_doc(self) -> str:
        """Read the usage guide bundled beside this package."""
        asset = Path(__file__).resolve().parent / "assets" / self._ASSET_NAME
        return asset.read_text(encoding="utf-8")
