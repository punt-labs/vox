"""Effective per-repo enablement state and personal-scope target guard.

One authoritative resolver (:class:`EnablementState`) reads the four
enablement markers from the git root and returns the effective state plus
the deciding file (``tool-enable-disable.md`` § 2.7 / TED-24). Every
consumer -- hooks, MCP tools, ``vox status``, and ``vox doctor`` -- calls
this resolver rather than re-deriving state.

:class:`PersonalTargetGuard` enforces the personal-scope safety contract
(§ 2.3 / TED-8): before any ``--scope local`` write or removal, every
target path and its resolved target must stay inside the repo, be ignored
by git, and not be tracked.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self, final

from punt_vox.dirs import find_repo_root

__all__ = [
    "EffectiveEnablement",
    "EnablementScope",
    "EnablementState",
    "EnablementStatus",
    "PersonalTargetGuard",
]

type EnablementScope = Literal["project", "local"]
type EnablementStatus = Literal[
    "enabled_project",
    "enabled_local",
    "disabled_local",
    "opted_out",
    "dormant",
    "absent",
]

_SHARED_DIR = Path(".punt-labs") / "vox"
_LOCAL_DIR = Path(".punt-labs") / "local" / "vox"


def _is_regular_file(path: Path) -> bool:
    """Return whether *path* is an existing regular file (symlinks count as absent)."""
    try:
        return path.is_file() and not path.is_symlink()
    except OSError:
        return False


def _is_dir(path: Path) -> bool:
    """Return whether *path* is an existing directory, tolerating permission errors."""
    try:
        return path.is_dir()
    except OSError:
        return False


@dataclass(frozen=True, slots=True)
class EffectiveEnablement:
    """Result of evaluating vox's enablement markers in one repository."""

    on: bool
    status: EnablementStatus
    deciding_file: Path | None
    shared_guide_loads: bool = False


@final
class EnablementState:
    """Resolve vox's effective enablement state from a repository's markers."""

    __slots__ = ()

    def __new__(cls) -> Self:
        """Create an enablement state resolver."""
        return super().__new__(cls)

    @classmethod
    def resolve(cls, repo_root: Path) -> EffectiveEnablement:
        """Evaluate the four enablement markers under *repo_root*.

        Precedence (TED-24):
        1. ``.punt-labs/vox/opt-out`` -> off (``opted_out``)
        2. ``.punt-labs/local/vox/disabled`` -> off (``disabled_local``)
        3. ``.punt-labs/vox/enabled`` -> on (``enabled_project``)
        4. ``.punt-labs/local/vox/enabled`` -> on (``enabled_local``)
        5. ``.punt-labs/vox/`` directory present -> off (``dormant``)
        6. Otherwise -> off (``absent``)
        """
        shared_dir = repo_root / _SHARED_DIR
        local_dir = repo_root / _LOCAL_DIR

        opt_out = shared_dir / "opt-out"
        if _is_regular_file(opt_out):
            return EffectiveEnablement(
                on=False,
                status="opted_out",
                deciding_file=opt_out,
                shared_guide_loads=False,
            )

        shared_enabled = shared_dir / "enabled"
        shared_present = _is_regular_file(shared_enabled)

        local_disabled = local_dir / "disabled"
        if _is_regular_file(local_disabled):
            return EffectiveEnablement(
                on=False,
                status="disabled_local",
                deciding_file=local_disabled,
                shared_guide_loads=shared_present,
            )

        if shared_present:
            return EffectiveEnablement(
                on=True,
                status="enabled_project",
                deciding_file=shared_enabled,
                shared_guide_loads=True,
            )

        local_enabled = local_dir / "enabled"
        if _is_regular_file(local_enabled):
            return EffectiveEnablement(
                on=True,
                status="enabled_local",
                deciding_file=local_enabled,
                shared_guide_loads=False,
            )

        if _is_dir(shared_dir):
            return EffectiveEnablement(
                on=False,
                status="dormant",
                deciding_file=None,
                shared_guide_loads=False,
            )

        return EffectiveEnablement(
            on=False,
            status="absent",
            deciding_file=None,
            shared_guide_loads=False,
        )

    @classmethod
    def resolve_cwd(cls, start: Path | None = None) -> EffectiveEnablement | None:
        """Resolve effective enablement for the git repo enclosing *start*.

        Returns ``None`` when *start* (default: cwd) is outside a git repo.
        """
        root = find_repo_root(start)
        if root is None:
            return None
        return cls.resolve(root)


@final
class PersonalTargetGuard:
    """Confirm every personal-scope target is ignored and untracked before touching it.

    Checks both the literal path and its symlink-resolved target against
    ``git check-ignore`` and ``git ls-files`` so a symlinked ``CLAUDE.local.md``
    pointing at ``CLAUDE.md`` (or outside the repository) is refused before any
    write or removal happens (TED-8).
    """

    __slots__ = ("_repo_root",)
    _repo_root: Path

    def __new__(cls, repo_root: Path) -> Self:
        """Bind *repo_root* for git ignore and tracking checks."""
        self = super().__new__(cls)
        self._repo_root = repo_root.resolve()
        return self

    def verify(self, *targets: Path) -> None:
        """Raise ``ValueError`` if any target is tracked, unignored, or escapes repo.

        Raises:
            ValueError: If a target resolves outside the repository, is not
                matched by gitignore, or is tracked in the git index.
        """
        for target in targets:
            self._verify_one(target)

    def _verify_one(self, target: Path) -> None:
        """Validate one path and its resolved target."""
        resolved = target.resolve()
        if not resolved.is_relative_to(self._repo_root):
            msg = (
                f"refusing personal-scope write to {target}: "
                f"resolves outside repository ({resolved})"
            )
            raise ValueError(msg)

        # Check both the logical path under the repo and the resolved target so a
        # symlink from an ignored name to a tracked file (e.g. CLAUDE.local.md ->
        # CLAUDE.md) is caught on the resolved path.
        candidates: list[Path] = []
        abs_target = target if target.is_absolute() else (self._repo_root / target)
        if abs_target.is_relative_to(self._repo_root):
            candidates.append(abs_target.relative_to(self._repo_root))
        rel_resolved = resolved.relative_to(self._repo_root)
        if rel_resolved not in candidates:
            candidates.append(rel_resolved)

        for rel in candidates:
            self._check_git_untracked(rel, target)
            self._check_git_ignored(rel, target)

    @staticmethod
    def _git_bin() -> str:
        """Return the resolved ``git`` executable path."""
        git = shutil.which("git")
        if git is None:
            msg = "git executable not found on PATH"
            raise ValueError(msg)
        return git

    def _check_git_ignored(self, rel: Path, original: Path) -> None:
        """Raise ``ValueError`` if *rel* is not ignored by git."""
        result = subprocess.run(
            [
                self._git_bin(),
                "-C",
                str(self._repo_root),
                "check-ignore",
                "-q",
                "--",
                str(rel),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            msg = (
                f"refusing personal-scope operation on {original}: "
                f"{rel} is not gitignored (add the gitignore baseline first)"
            )
            raise ValueError(msg)

    def _check_git_untracked(self, rel: Path, original: Path) -> None:
        """Raise ``ValueError`` if *rel* is tracked in the git index."""
        result = subprocess.run(
            [
                self._git_bin(),
                "-C",
                str(self._repo_root),
                "ls-files",
                "--error-unmatch",
                "--",
                str(rel),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            msg = (
                f"refusing personal-scope operation on {original}: "
                f"{rel} is tracked in git"
            )
            raise ValueError(msg)
