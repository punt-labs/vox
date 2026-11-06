"""Ask git which paths it ignores, by every ignore source git itself honours.

A secret is often kept out of git by an ignore rule -- in the root
``.gitignore``, in a ``.gitignore`` any number of directories down, in
``.git/info/exclude``, or in the user's ``core.excludesFile`` -- and the
call agent must not read what git was told to keep out. Re-implementing
gitignore is how a nested rule gets missed, so :class:`GitIgnores` asks git:
``git check-ignore`` applies every source with git's own scoping, negation,
and anchoring.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING, Self, final

from punt_vox.voxd.conversation_mode.call_agent_errors import CallAgentSpawnError

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

__all__ = ["GitIgnoreError", "GitIgnores"]

# Seconds git may take to answer before it counts as not answering.
_TIMEOUT_S = 15.0

# --no-index: a tracked file a rule matches counts as ignored too -- denial
# is the safe side of that difference.
_CHECK_IGNORE = ("git", "check-ignore", "--no-index", "-z", "--stdin")


class GitIgnoreError(RuntimeError):
    """git could not say which paths it ignores; the caller must deny."""


@final
class GitIgnores:
    """The ignore rules git applies under one root, asked of git itself."""

    __slots__ = ("_active", "_root")
    _root: Path
    _active: bool

    def __new__(cls, root: Path, *, active: bool) -> Self:
        """Ask git about paths under *root*; an inactive one ignores nothing."""
        self = super().__new__(cls)
        self._root = root
        self._active = active
        return self

    @classmethod
    def for_root(cls, root: Path) -> Self:
        """Return *root*'s rules: active in a git work tree, inactive outside one.

        Raises :class:`~.call_agent_errors.CallAgentSpawnError` when git
        cannot run or cannot say which of the two *root* is: a call whose
        ignore rules are unknown must not start.
        """
        argv = ("git", "-C", str(root), "rev-parse", "--is-inside-work-tree")
        try:
            result = subprocess.run(
                argv, capture_output=True, text=True, timeout=_TIMEOUT_S, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            msg = f"cannot run git to learn what {root} ignores: {exc}"
            raise CallAgentSpawnError(msg) from exc
        if result.returncode == 0 and result.stdout.strip() == "true":
            return cls(root, active=True)
        if "not a git repository" in result.stderr:
            return cls(root, active=False)
        detail = result.stderr.strip() or f"exit {result.returncode}"
        msg = f"git cannot say whether {root} is a work tree: {detail}"
        raise CallAgentSpawnError(msg)

    @property
    def active(self) -> bool:
        """Whether *root* lies in a git work tree, so git's ignore rules apply."""
        return self._active

    def ignored(self, paths: Iterable[str]) -> frozenset[str]:
        """Return those of *paths* (relative to the root, ``/``-separated) git ignores.

        Raises :class:`GitIgnoreError` when git does not answer.
        """
        asked = list(dict.fromkeys(path for path in paths if path))
        if not self._active or not asked:
            return frozenset()
        try:
            result = subprocess.run(
                _CHECK_IGNORE,
                cwd=self._root,
                input="\0".join(asked) + "\0",
                capture_output=True,
                text=True,
                timeout=_TIMEOUT_S,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            msg = f"git check-ignore did not run: {exc}"
            raise GitIgnoreError(msg) from exc
        # 0: some paths are ignored; 1: none are; anything else: no answer.
        if result.returncode not in {0, 1}:
            detail = result.stderr.strip()
            msg = f"git check-ignore exited {result.returncode}: {detail}"
            raise GitIgnoreError(msg)
        return frozenset(path for path in result.stdout.split("\0") if path)
