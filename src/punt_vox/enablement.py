"""Per-repo vox enablement state machine (``tool-enable-disable.md``).

:class:`RepoEnablement` is the single engine behind CLI ``vox enable`` /
``vox disable``, MCP ``mic:enablement``, and slash ``/vox enable`` /
``/vox disable``. It supports both scopes:

- ``local`` (default): personal enablement in gitignored paths
  (``.punt-labs/local/vox/``, ``CLAUDE.local.md``, ``.claude/settings.local.json``),
  verified ignored and untracked via :class:`PersonalTargetGuard` before any write.
- ``project``: shared repository enablement
  (``.punt-labs/vox/``, ``CLAUDE.md``, ``.claude/settings.json``, ``.gitignore``).

Enable never writes personal preferences (``notify``, ``provider``) into durable
``vox.md``, deposits the guide only when absent (or on ``--refresh``), and
refuses when ``.punt-labs/vox/opt-out`` is present unless cleared with
``--scope project --clear-opt-out``.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Self, final

from punt_vox.claude_md import ClaudeMdImport
from punt_vox.deposited_files import DepositedGuide, VoxMarker
from punt_vox.dirs import find_repo_root
from punt_vox.enablement_state import (
    EffectiveEnablement,
    EnablementScope,
    EnablementState,
    PersonalTargetGuard,
)
from punt_vox.gitignore_entry import VoxGitignore
from punt_vox.settings_registration import SettingsRegistration

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["RepoEnablement"]

_SHARED_IMPORT_LINE = "@.punt-labs/vox/CLAUDE.md"
_LOCAL_IMPORT_LINE = "@.punt-labs/local/vox/CLAUDE.md"


@final
class RepoEnablement:
    """Scoped enable/disable/purge state machine for one repository."""

    __slots__ = (
        "_gitignore",
        "_guard",
        "_guide",
        "_import",
        "_local_disabled",
        "_marker",
        "_opt_out",
        "_repo_root",
        "_scope",
        "_settings",
        "_shared_marker",
    )

    _repo_root: Path
    _scope: EnablementScope
    _import: ClaudeMdImport
    _marker: VoxMarker
    _shared_marker: VoxMarker
    _opt_out: VoxMarker
    _local_disabled: VoxMarker
    _guide: DepositedGuide
    _settings: SettingsRegistration
    _gitignore: VoxGitignore
    _guard: PersonalTargetGuard | None

    def __new__(
        cls,
        repo_root: Path,
        *,
        scope: EnablementScope = "local",
        import_writer: ClaudeMdImport,
        marker: VoxMarker,
        shared_marker: VoxMarker,
        opt_out: VoxMarker,
        local_disabled: VoxMarker,
        guide: DepositedGuide,
        settings: SettingsRegistration,
        gitignore: VoxGitignore,
        guard: PersonalTargetGuard | None = None,
    ) -> Self:
        """Assemble a repo enablement state machine from its components."""
        self = super().__new__(cls)
        self._repo_root = repo_root
        self._scope = scope
        self._import = import_writer
        self._marker = marker
        self._shared_marker = shared_marker
        self._opt_out = opt_out
        self._local_disabled = local_disabled
        self._guide = guide
        self._settings = settings
        self._gitignore = gitignore
        self._guard = guard
        return self

    @classmethod
    def for_repo(
        cls,
        repo_root: Path,
        *,
        scope: EnablementScope = "local",
    ) -> Self:
        """Bind every vox enablement artifact under *repo_root* for *scope*."""
        shared_dir = repo_root / ".punt-labs" / "vox"
        local_dir = repo_root / ".punt-labs" / "local" / "vox"
        shared_marker = VoxMarker(shared_dir / "enabled", repo_root)
        opt_out = VoxMarker.opt_out(shared_dir / "opt-out", repo_root)
        local_disabled = VoxMarker.local_disabled(local_dir / "disabled", repo_root)
        gitignore = VoxGitignore(repo_root)

        if scope == "project":
            return cls(
                repo_root,
                scope="project",
                import_writer=ClaudeMdImport(
                    repo_root / "CLAUDE.md", _SHARED_IMPORT_LINE
                ),
                marker=shared_marker,
                shared_marker=shared_marker,
                opt_out=opt_out,
                local_disabled=local_disabled,
                guide=DepositedGuide(shared_dir / "CLAUDE.md", repo_root),
                settings=SettingsRegistration(repo_root / ".claude" / "settings.json"),
                gitignore=gitignore,
                guard=None,
            )

        return cls(
            repo_root,
            scope="local",
            import_writer=ClaudeMdImport(
                repo_root / "CLAUDE.local.md", _LOCAL_IMPORT_LINE
            ),
            marker=VoxMarker(local_dir / "enabled", repo_root),
            shared_marker=shared_marker,
            opt_out=opt_out,
            local_disabled=local_disabled,
            guide=DepositedGuide(local_dir / "CLAUDE.md", repo_root),
            settings=SettingsRegistration(
                repo_root / ".claude" / "settings.local.json"
            ),
            gitignore=gitignore,
            guard=PersonalTargetGuard(repo_root),
        )

    @classmethod
    def for_cwd(cls, *, scope: EnablementScope = "local") -> Self:
        """Bind every vox enablement artifact for the git repo enclosing cwd.

        Raises:
            ValueError: If cwd is not inside a git repository.
        """
        root = find_repo_root()
        if root is None:
            msg = "not inside a git repository; run from inside a repo"
            raise ValueError(msg)
        return cls.for_repo(root, scope=scope)

    @property
    def root(self) -> Path:
        """Return the repository root path."""
        return self._repo_root

    @property
    def marker_path(self) -> Path:
        """Return the scope's enabled marker path."""
        return self._marker.path

    @property
    def settings_path(self) -> Path:
        """Return the scope's settings file path."""
        return self._settings.path

    @property
    def permission_rules(self) -> Mapping[str, tuple[str, ...]]:
        """Return the permission rules owned by this enablement."""
        return self._settings.rules

    @property
    def import_line(self) -> str:
        """Return the canonical ``@``-import line for this scope."""
        return self._import.import_line

    @property
    def effective_state(self) -> EffectiveEnablement:
        """Return the repository's current effective enablement state."""
        return EnablementState.resolve(self._repo_root)

    def is_enabled(self) -> bool:
        """Return whether vox is effectively on in this repository."""
        return EnablementState.resolve(self._repo_root).on

    def _verify_personal_targets(self) -> None:
        """Verify all personal-scope targets are gitignored and untracked (TED-8)."""
        if self._guard is None:
            return
        self._guard.verify(
            self._marker.path,
            self._guide.path,
            self._local_disabled.path,
            self._import.path,
            self._settings.path,
        )

    def enable(
        self,
        *,
        refresh: bool = False,
        clear_opt_out: bool = False,
    ) -> EffectiveEnablement:
        """Turn vox on in this repository for the configured scope.

        Refuses if ``.punt-labs/vox/opt-out`` exists unless ``scope == "project"``
        and ``clear_opt_out=True`` (TED-23). Plain ``enable`` deposits the guide
        only when absent; ``refresh=True`` updates the guide in place (TED-11).
        """
        if clear_opt_out and self._scope != "project":
            msg = "--clear-opt-out requires --scope project"
            raise ValueError(msg)

        if self._opt_out.is_present():
            if not clear_opt_out:
                msg = (
                    f"vox is opted out in this repository ({self._opt_out.path}); "
                    "run 'vox enable --scope project --clear-opt-out' to re-enable"
                )
                raise ValueError(msg)
            self._opt_out.remove()

        if self._scope == "local":
            self._enable_local(refresh=refresh)
        else:
            self._enable_project(refresh=refresh)
        return EnablementState.resolve(self._repo_root)

    def _enable_local(self, *, refresh: bool) -> None:
        """Execute personal-scope enable (TED-8, TED-9)."""
        if self._shared_marker.is_present():
            if self._guard is not None:
                self._guard.verify(self._local_disabled.path)
            self._local_disabled.remove()
            return
        self._verify_personal_targets()
        self._local_disabled.remove()
        self._guide.deposit(refresh=refresh)
        self._import.register()
        self._settings.register()
        self._marker.write()

    def _enable_project(self, *, refresh: bool) -> None:
        """Execute shared-scope enable (TED-7, TED-9, TED-11)."""
        self._gitignore.ensure()
        self._guide.deposit(refresh=refresh)
        self._import.register()
        self._settings.register()
        self._marker.write()

    def disable(self, *, opt_out: bool = False) -> EffectiveEnablement:
        """Turn vox off for the configured scope (TED-10, TED-23)."""
        if opt_out and self._scope != "project":
            msg = "--opt-out requires --scope project"
            raise ValueError(msg)

        if self._scope == "local":
            self._disable_local()
        else:
            self._disable_project(opt_out=opt_out)
        return EnablementState.resolve(self._repo_root)

    def _disable_local(self) -> None:
        """Execute personal-scope disable (TED-8, TED-10)."""
        self._verify_personal_targets()
        self._import.prune()
        self._marker.remove()
        self._settings.deregister()
        if self._shared_marker.is_present():
            self._local_disabled.write()

    def _disable_project(self, *, opt_out: bool) -> None:
        """Execute shared-scope disable and optional opt-out (TED-10, TED-23)."""
        self._import.prune()
        self._marker.remove()
        self._settings.deregister()
        if opt_out:
            self._opt_out.write()

    def purge(self) -> EffectiveEnablement:
        """Disable and delete the scope's vox directory subtree."""
        self.disable()
        if self._scope == "local":
            self._local_disabled.remove()
        target_dir = self._marker.path.parent
        if target_dir.is_symlink():
            msg = f"refusing to purge symlinked directory {target_dir}"
            raise ValueError(msg)
        if target_dir.exists():
            shutil.rmtree(target_dir)
        return EnablementState.resolve(self._repo_root)
